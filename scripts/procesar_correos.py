"""
Pasa todos los correos de datos/correos.sqlite por los extractores y guarda
los movimientos de dinero en la tabla "movimientos" de la misma base.

Uso (despues de leer_mbox.py o sincronizar_gmail.py):
    python procesar_correos.py --salida "C:\\ruta\\a\\datos"

Genera en una carpeta nueva de esta corrida:
    movimientos.xlsx   hojas: movimientos, por_mes, no_reconocidos, sin_verificar
    procesar_correos.log

Se puede correr las veces que quieras: la tabla "movimientos" se rehace
completa desde los correos, asi que nunca duplica.
"""

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

from extractores import extraer
from leer_mbox import CARPETA_DATOS, carpeta_de_corrida, iniciar_log


def verificado(autenticacion):
    """True si Gmail confirmo que el correo viene del dominio del banco (DKIM o SPF)."""
    a = (autenticacion or "").lower()
    if not a:
        return None  # el correo no trae la cabecera; no se puede saber
    return "dkim=pass" in a or "spf=pass" in a


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--salida", type=Path, default=CARPETA_DATOS, help="carpeta con correos.sqlite")
    args = p.parse_args()

    carpeta_corrida = carpeta_de_corrida(args.salida, Path(__file__).stem)
    iniciar_log(carpeta_corrida, Path(__file__).stem)

    con = sqlite3.connect(args.salida / "correos.sqlite")
    correos = pd.read_sql("SELECT id, fecha, remitente_correo, dominio, asunto, tipo_asunto, autenticacion, cuerpo "
                          "FROM correos", con, parse_dates=["fecha"])
    print(f"{len(correos)} correos por revisar.")

    movimientos, clasificacion = [], []
    for c in correos.itertuples(index=False):
        fecha_correo = c.fecha.to_pydatetime() if pd.notna(c.fecha) else None
        try:
            r = extraer(c.remitente_correo, c.asunto, c.cuerpo, fecha_correo)
        except Exception as error:  # un correo raro no debe detener todo
            print(f"  Error en correo {c.id} ({c.asunto[:50]}): {error}")
            clasificacion.append({"correo_id": c.id, "estado": "error", "regla": str(error)[:200]})
            continue
        clasificacion.append({"correo_id": c.id, "estado": r.estado, "regla": r.regla})
        if r.movimiento:
            fila = r.movimiento.a_dict()
            fila["detalle"] = json.dumps(fila["detalle"], ensure_ascii=False)
            fila["correo_id"] = c.id
            fila["fecha_correo"] = c.fecha
            fila["remitente_verificado"] = verificado(c.autenticacion)
            if fila["fecha"] is None:
                fila["fecha"] = c.fecha
                fila["confianza"] = round(fila["confianza"] * 0.8, 2)
            movimientos.append(fila)

    mov = pd.DataFrame(movimientos)
    cla = pd.DataFrame(clasificacion)
    if mov.empty:
        print("No se encontraron movimientos.")
        return
    mov["fecha"] = pd.to_datetime(mov["fecha"])
    antes = len(mov)
    mov = mov.drop_duplicates(subset=["banco", "tipo", "fecha", "monto", "moneda", "ultimos4", "autorizacion", "referencia"])
    mov = mov.sort_values("fecha")
    mov["mes"] = mov["fecha"].dt.strftime("%Y-%m")

    mov.to_sql("movimientos", con, if_exists="replace", index=False)
    cla.to_sql("clasificacion", con, if_exists="replace", index=False)
    con.close()

    # Resumenes
    con_monto = mov[mov["direccion"].isin(["salida", "entrada"])]
    por_mes = con_monto.pivot_table(index="mes", columns=["direccion", "moneda"], values="monto",
                                    aggfunc="sum", fill_value=0).round(2)
    no_rec = (cla[cla["estado"].isin(["no_reconocido", "error"])].merge(correos, left_on="correo_id", right_on="id")
              .groupby(["dominio", "tipo_asunto", "regla"]).size().rename("correos").reset_index()
              .sort_values("correos", ascending=False))
    sin_verificar = mov[mov["remitente_verificado"] == False]  # noqa: E712

    with pd.ExcelWriter(carpeta_corrida / "movimientos.xlsx") as xls:
        mov.drop(columns=["fecha_correo"]).to_excel(xls, sheet_name="movimientos", index=False)
        por_mes.to_excel(xls, sheet_name="por_mes")
        no_rec.to_excel(xls, sheet_name="no_reconocidos", index=False)
        sin_verificar.to_excel(xls, sheet_name="sin_verificar", index=False)

    print("\nCorreos por resultado:")
    print(cla.groupby(["estado", "regla"]).size().to_string())
    print(f"\n{len(mov)} movimientos guardados ({antes - len(mov)} duplicados quitados).")
    print("\nMovimientos por banco y tipo:")
    print(mov.groupby(["banco", "tipo", "moneda"]).agg(cantidad=("monto", "size"), total=("monto", "sum"))
          .round(2).to_string())
    if not no_rec.empty:
        print(f"\nATENCION: {int(no_rec['correos'].sum())} correos de bancos con formato no reconocido "
              f"(ver hoja no_reconocidos). Mandame ejemplos con muestras_anonimas.py.")
    if not sin_verificar.empty:
        print(f"\nATENCION: {len(sin_verificar)} movimientos de correos que NO pasaron la verificacion "
              f"DKIM/SPF (posible correo falso). Revisa la hoja sin_verificar.")
    print(f"\nResultados en: {carpeta_corrida.resolve()}")


if __name__ == "__main__":
    main()
