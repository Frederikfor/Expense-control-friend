"""
Saca la estructura de los estados de cuenta en PDF de cada banco, con los
datos personales tapados, para poder escribir el lector de cada banco.

Uso:
    python muestras_pdf.py --salida "..\\estados de cuenta" --ocultar "Nombre,Apellido1,Apellido2"

Paso 1 (este): elige los PDF que de verdad vienen de un banco. No se fia del
asunto "estado de cuenta" (muy generico): un PDF entra solo si
  - el correo que lo trajo es de un dominio oficial (extractores.DOMINIOS_BANCO), y
  - Gmail confirmo que el correo viene de ese dominio (DKIM o SPF).
Los demas PDF (CCSS, pensiones, documentos personales) no se abren.

Genera en una carpeta nueva de esta corrida:
    seleccion_pdf.txt   PDF de bancos agrupados por asunto y patron de nombre
                        (digitos -> 9). Revisalo antes de compartirlo.
    muestras_pdf.log    En consola y en el log solo salen conteos.
"""

import argparse
import hashlib
import re
import sqlite3
from pathlib import Path

import pandas as pd

from extractores import DOMINIOS_BANCO
from leer_mbox import CARPETA_DATOS, carpeta_de_corrida, iniciar_log
from muestras_anonimas import tapar
from procesar_correos import verificado


def patron_de_nombre(nombre_archivo):
    """'0117d82e49__Extracto_202005_0000.pdf' -> '_extracto_999999_9999.pdf' (sin el id del correo)."""
    sin_id = nombre_archivo.split("_", 1)[1] if "_" in nombre_archivo else nombre_archivo
    return re.sub(r"\d", "9", sin_id).lower()


def categoria_pdf(dominio, es_verificado, hay_correo):
    """Decide si un PDF se usa. Solo 'banco' se abre en los pasos siguientes."""
    if not hay_correo:
        return "sin_correo"          # el PDF no tiene su correo en la base
    if dominio not in DOMINIOS_BANCO:
        return "otro_remitente"
    if es_verificado is not True:
        return "banco_sin_verificar"  # dice ser del banco pero sin DKIM/SPF: posible correo falso
    return "banco"


def elegir_pdfs(carpeta):
    """Cruza los PDF de carpeta/adjuntos con su correo en carpeta/correos.sqlite.
    Devuelve una fila por PDF con banco, asunto, categoria y huella (para duplicados)."""
    archivos = [a for a in (carpeta / "adjuntos").iterdir() if a.suffix.lower() == ".pdf"]
    with sqlite3.connect(carpeta / "correos.sqlite") as con:
        correos = pd.read_sql("SELECT id, fecha, dominio, asunto, tipo_asunto, autenticacion FROM correos",
                              con, parse_dates=["fecha"])
    correos = correos.drop_duplicates(subset="id").set_index("id")

    filas = []
    for archivo in archivos:
        correo_id = archivo.name.split("_", 1)[0]
        hay_correo = correo_id in correos.index
        c = correos.loc[correo_id] if hay_correo else None
        dominio = c["dominio"] if hay_correo else ""
        es_verificado = verificado(c["autenticacion"]) if hay_correo else None
        filas.append({
            "archivo": archivo,
            "correo_id": correo_id,
            "dominio": dominio,
            "banco": DOMINIOS_BANCO.get(dominio, ""),
            "fecha": c["fecha"] if hay_correo else pd.NaT,
            "asunto": c["asunto"] if hay_correo else "",
            "tipo_asunto": c["tipo_asunto"] if hay_correo else "",
            "categoria": categoria_pdf(dominio, es_verificado, hay_correo),
            "patron": patron_de_nombre(archivo.name),
            # Mismo contenido en dos correos = mismo estado de cuenta repetido
            "huella": hashlib.sha1(archivo.read_bytes()).hexdigest(),
        })
    return pd.DataFrame(filas)


def escribir_seleccion(pdfs, destino, nombres):
    """Lista para revisar: por banco, cada combinacion asunto + patron de nombre, sin datos personales."""
    bancos = pdfs[pdfs["categoria"] == "banco"]
    lineas = []
    for banco, grupo in bancos.groupby("banco"):
        lineas += ["=" * 78, f"BANCO: {banco}   |   {len(grupo)} PDF ({grupo['huella'].nunique()} distintos)", "=" * 78]
        resumen = (grupo.groupby(["tipo_asunto", "patron"])
                        .agg(pdf=("archivo", "size"), desde=("fecha", "min"), hasta=("fecha", "max"))
                        .reset_index().sort_values("pdf", ascending=False))
        for _, r in resumen.iterrows():
            lineas += [
                f"Asunto:  {tapar(r['tipo_asunto'], nombres)}",
                f"Archivo: {tapar(r['patron'], nombres)}",
                f"         {r['pdf']} PDF, {r['desde']:%Y-%m} a {r['hasta']:%Y-%m}",
                "-" * 40,
            ]
    destino.write_text("\n".join(lineas), encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--salida", type=Path, default=CARPETA_DATOS, help="carpeta con correos.sqlite y adjuntos\\")
    p.add_argument("--ocultar", default="", help="nombres a tapar separados por coma (tu nombre y apellidos)")
    args = p.parse_args()

    nombres = sorted({n.strip() for n in args.ocultar.split(",") if n.strip()}, key=len, reverse=True)
    carpeta_corrida = carpeta_de_corrida(args.salida, Path(__file__).stem)
    iniciar_log(carpeta_corrida, Path(__file__).stem)

    pdfs = elegir_pdfs(args.salida)
    print(f"{len(pdfs)} PDF en adjuntos.\n")

    print("PDF por resultado del filtro:")
    print(pdfs.groupby("categoria").size().to_string())

    bancos = pdfs[pdfs["categoria"] == "banco"]
    if not bancos.empty:
        print("\nPDF de bancos (verificados), por banco:")
        print(bancos.groupby("banco").agg(pdf=("archivo", "size"), distintos=("huella", "nunique"),
                                          asuntos=("tipo_asunto", "nunique"), patrones=("patron", "nunique"),
                                          desde=("fecha", "min"), hasta=("fecha", "max")).to_string())
        print("\nPDF de bancos por anio:")
        print(bancos.groupby([bancos["fecha"].dt.year.rename("anio"), "banco"]).size()
                    .unstack(fill_value=0).to_string())

    sin_verificar = pdfs[pdfs["categoria"] == "banco_sin_verificar"]
    if not sin_verificar.empty:
        print(f"\nATENCION: {len(sin_verificar)} PDF dicen venir de un banco pero el correo no paso DKIM/SPF. "
              "No se usan.")

    print("\nPDF descartados por dominio del remitente (no se abren):")
    otros = pdfs[pdfs["categoria"] == "otro_remitente"]
    print(otros.groupby("dominio").size().sort_values(ascending=False).to_string() if not otros.empty else "  ninguno")

    destino = carpeta_corrida / "seleccion_pdf.txt"
    escribir_seleccion(pdfs, destino, nombres)
    print(f"\nDetalle por asunto y nombre de archivo en: {destino.resolve()}")
    print("Revisalo antes de compartirlo.")


if __name__ == "__main__":
    main()
