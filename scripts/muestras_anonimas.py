"""
Saca ejemplos de cada tipo de correo de cada banco, con los datos personales
tapados, para poder escribir los lectores (extractores) de cada banco.

Uso:
    python muestras_anonimas.py --salida "C:\\ruta\\a\\datos" --ocultar "Nombre,Apellido1,Apellido2"

Genera muestras.txt dentro de una carpeta nueva de esta corrida. Revisalo
antes de compartirlo: los numeros se cambian por 9 (se conserva el formato,
ej. 12.500,00 -> 99.999,99), los correos y los nombres de --ocultar se tapan.
"""

import argparse
import re
import sqlite3
from pathlib import Path

import pandas as pd

from leer_mbox import CARPETA_DATOS, carpeta_de_corrida, iniciar_log

MAX_CARACTERES = 1500


def tapar(texto, nombres):
    texto = re.sub(r"[\w.+-]+@([\w-]+\.[\w.-]+)", r"xxxx@\1", texto)  # correos: deja solo el dominio
    for nombre in nombres:
        texto = re.sub(re.escape(nombre), "[NOMBRE]", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\d", "9", texto)  # montos, tarjetas, cedula, telefonos, fechas
    texto = re.sub(r"\n\s*\n+", "\n", texto)  # quita lineas vacias repetidas
    return texto.strip()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--salida", type=Path, default=CARPETA_DATOS, help="carpeta con correos.sqlite")
    p.add_argument("--ocultar", default="", help="nombres a tapar separados por coma (tu nombre y apellidos)")
    p.add_argument("--max-tipos", type=int, default=15, help="maximo de tipos de asunto por banco")
    p.add_argument("--ejemplos", type=int, default=2, help="ejemplos por tipo de asunto")
    args = p.parse_args()

    nombres = sorted({n.strip() for n in args.ocultar.split(",") if n.strip()}, key=len, reverse=True)
    carpeta_corrida = carpeta_de_corrida(args.salida, Path(__file__).stem)
    iniciar_log(carpeta_corrida, Path(__file__).stem)

    with sqlite3.connect(args.salida / "correos.sqlite") as con:
        df = pd.read_sql("SELECT dominio, remitente_correo, asunto, tipo_asunto, fecha, adjuntos, cuerpo FROM correos",
                         con, parse_dates=["fecha"])

    conteo = df.groupby(["dominio", "tipo_asunto"]).size().rename("correos").reset_index()
    conteo = conteo.sort_values(["dominio", "correos"], ascending=[True, False])
    conteo = conteo.groupby("dominio").head(args.max_tipos)

    lineas = []
    for _, tipo in conteo.iterrows():
        grupo = df[(df["dominio"] == tipo["dominio"]) & (df["tipo_asunto"] == tipo["tipo_asunto"])]
        grupo = grupo.sort_values("fecha", ascending=False).head(args.ejemplos)
        lineas.append("=" * 78)
        lineas.append(f"BANCO/DOMINIO: {tipo['dominio']}   |   TIPO: {tapar(tipo['tipo_asunto'], nombres)}"
                      f"   |   {tipo['correos']} correos")
        lineas.append("=" * 78)
        for _, c in grupo.iterrows():
            cuerpo = tapar(c["cuerpo"] or "", nombres)
            if len(cuerpo) > MAX_CARACTERES:
                cuerpo = cuerpo[:MAX_CARACTERES] + "\n[... recortado ...]"
            lineas += [
                f"Remitente: {c['remitente_correo']}",
                f"Asunto: {tapar(c['asunto'], nombres)}",
                f"Adjuntos: {tapar(c['adjuntos'] or '-', nombres)}",
                "--- cuerpo ---",
                cuerpo,
                "-" * 40,
            ]

    destino = carpeta_corrida / "muestras.txt"
    destino.write_text("\n".join(lineas), encoding="utf-8")
    print(f"{len(conteo)} tipos de correo en {conteo['dominio'].nunique()} dominios.")
    print(f"Muestras en: {destino.resolve()}")
    print("Revisalo antes de compartirlo: busca nombres, direcciones u otros datos que no se hayan tapado.")


if __name__ == "__main__":
    main()
