"""
Convierte el .mbox de Google Takeout en tablas para analizar.

Uso (en Windows, dentro de tu entorno virtual):
    pip install pandas openpyxl
    python leer_mbox.py "C:\\ruta\\a\\banco.mbox" --salida "C:\\ruta\\a\\datos"

Genera en la carpeta de salida (por defecto, datos\\ junto a scripts\\):
    correos.sqlite      tabla "correos" con todo (fecha, remitente, asunto, texto)
    adjuntos\\           PDFs y XML adjuntos (estados de cuenta, facturas)
    leer_mbox_AAAAMMDD_HHMM\\   resumen.xlsx (hojas: por_remitente, por_mes,
                        asuntos_frecuentes) y leer_mbox.log de esta corrida

Todo corre en tu laptop. No envia nada a internet.
"""

import argparse
import email.utils
import hashlib
import html
import mailbox
import re
import sqlite3
import sys
from datetime import datetime
from email.header import decode_header, make_header
from pathlib import Path

import pandas as pd

EXTENSIONES_ADJUNTOS = {".pdf", ".xml"}
# Carpeta "datos" junto a scripts/, para que --salida funcione desde cualquier carpeta.
CARPETA_DATOS = Path(__file__).resolve().parents[1] / "datos"


def decodificar(valor):
    """Decodifica cabeceras tipo =?UTF-8?B?...?= a texto normal."""
    if valor is None:
        return ""
    try:
        return str(make_header(decode_header(str(valor))))
    except Exception:
        return str(valor)


def html_a_texto(contenido):
    contenido = re.sub(r"(?is)<(script|style).*?</\1>", " ", contenido)
    contenido = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</div>", "\n", contenido)
    contenido = re.sub(r"<[^>]+>", " ", contenido)
    contenido = html.unescape(contenido)
    return re.sub(r"[ \t\xa0]+", " ", contenido).strip()


def bytes_a_texto(parte):
    datos = parte.get_payload(decode=True) or b""
    charset = parte.get_content_charset() or "utf-8"
    try:
        return datos.decode(charset, errors="replace")
    except LookupError:
        return datos.decode("utf-8", errors="replace")


def extraer_cuerpo_y_adjuntos(mensaje, carpeta_adjuntos, id_corto):
    texto_plano, texto_html, adjuntos = [], [], []
    for parte in mensaje.walk():
        if parte.is_multipart():
            continue
        nombre = decodificar(parte.get_filename()) if parte.get_filename() else ""
        tipo = parte.get_content_type()
        if nombre:
            ext = Path(nombre).suffix.lower()
            adjuntos.append(nombre)
            if ext in EXTENSIONES_ADJUNTOS:
                nombre_seguro = re.sub(r"[^\w.\-]", "_", nombre)
                destino = carpeta_adjuntos / f"{id_corto}_{nombre_seguro}"
                if not destino.exists():
                    destino.write_bytes(parte.get_payload(decode=True) or b"")
        elif tipo == "text/plain":
            texto_plano.append(bytes_a_texto(parte))
        elif tipo == "text/html":
            texto_html.append(html_a_texto(bytes_a_texto(parte)))
    cuerpo = "\n".join(texto_plano) if texto_plano else "\n".join(texto_html)
    return cuerpo.strip(), adjuntos


def normalizar_asunto(asunto):
    """Quita numeros y montos para agrupar asuntos del mismo 'tipo'."""
    a = asunto.lower()
    a = re.sub(r"\d[\d.,]*", "#", a)
    return re.sub(r"\s+", " ", a).strip()


def mensaje_a_fila(msg, carpeta_adjuntos, id_respaldo):
    """Convierte un correo (email.message.Message) en una fila de la tabla."""
    id_mensaje = msg.get("Message-ID", "") or id_respaldo
    id_corto = hashlib.sha1(id_mensaje.encode()).hexdigest()[:10]
    nombre_rem, correo_rem = email.utils.parseaddr(decodificar(msg.get("From")))
    try:
        fecha = email.utils.parsedate_to_datetime(msg.get("Date"))
    except (TypeError, ValueError):
        fecha = None
    cuerpo, adjuntos = extraer_cuerpo_y_adjuntos(msg, carpeta_adjuntos, id_corto)
    asunto = decodificar(msg.get("Subject"))
    return {
        "id": id_corto,
        "message_id": id_mensaje,
        "fecha": fecha,
        "remitente_nombre": nombre_rem,
        "remitente_correo": correo_rem.lower(),
        "dominio": correo_rem.lower().split("@")[-1] if "@" in correo_rem else "",
        "asunto": asunto,
        "tipo_asunto": normalizar_asunto(asunto),
        "etiquetas_gmail": decodificar(msg.get("X-Gmail-Labels")),
        "autenticacion": decodificar(msg.get("Authentication-Results"))[:500],
        "adjuntos": "; ".join(adjuntos),
        "cuerpo": cuerpo,
    }


def filas_a_tabla(filas):
    df = pd.DataFrame(filas).drop_duplicates(subset="message_id")
    # Fechas a hora de Costa Rica, sin zona, para que Excel y SQLite las entiendan
    df["fecha"] = pd.to_datetime(df["fecha"], utc=True, errors="coerce").dt.tz_convert("America/Costa_Rica").dt.tz_localize(None)
    df = df.sort_values("fecha")
    df["mes"] = df["fecha"].dt.strftime("%Y-%m")
    return df


def carpeta_de_corrida(base, nombre_script):
    """Crea base/<script>_AAAAMMDD_HHMM (ej. sincronizar_gmail_20261005_1432) para los
    archivos de esta corrida, asi nunca se pisa ni se bloquea un resumen anterior."""
    carpeta = base / f"{nombre_script}_{datetime.now():%Y%m%d_%H%M}"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


class _Duplicar:
    """Escribe en la consola y en el archivo de log a la vez."""

    def __init__(self, consola, archivo):
        self.consola, self.archivo = consola, archivo

    def write(self, texto):
        self.consola.write(texto)
        self.archivo.write(texto)
        return len(texto)

    def flush(self):
        self.consola.flush()
        self.archivo.flush()

    def __getattr__(self, nombre):
        return getattr(self.consola, nombre)


def iniciar_log(carpeta, nombre_script):
    """Copia todo lo que sale en consola (incluidos los errores) a carpeta/<script>.log."""
    archivo = open(carpeta / f"{nombre_script}.log", "w", encoding="utf-8", buffering=1)
    sys.stdout = _Duplicar(sys.stdout, archivo)
    sys.stderr = _Duplicar(sys.stderr, archivo)
    print(f"Log de {nombre_script} - {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"Argumentos: {' '.join(sys.argv[1:])}\n")


def generar_resumen(df, salida):
    """Crea resumen.xlsx en 'salida' y devuelve la tabla por remitente."""
    por_remitente = (df.groupby(["dominio", "remitente_correo"])
                       .agg(correos=("id", "count"), primero=("fecha", "min"), ultimo=("fecha", "max"))
                       .reset_index().sort_values("correos", ascending=False))
    por_mes = df.pivot_table(index="mes", columns="dominio", values="id", aggfunc="count", fill_value=0)
    asuntos = (df.groupby(["dominio", "tipo_asunto"]).agg(correos=("id", "count"), ejemplo=("asunto", "first"))
                 .reset_index().sort_values("correos", ascending=False))

    with pd.ExcelWriter(salida / "resumen.xlsx") as xls:
        por_remitente.to_excel(xls, sheet_name="por_remitente", index=False)
        por_mes.to_excel(xls, sheet_name="por_mes")
        asuntos.to_excel(xls, sheet_name="asuntos_frecuentes", index=False)
    return por_remitente


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mbox", type=Path, help="archivo .mbox de Google Takeout")
    p.add_argument("--salida", type=Path, default=CARPETA_DATOS, help="carpeta de salida")
    args = p.parse_args()

    args.salida.mkdir(parents=True, exist_ok=True)
    carpeta_adjuntos = args.salida / "adjuntos"
    carpeta_adjuntos.mkdir(exist_ok=True)
    carpeta_corrida = carpeta_de_corrida(args.salida, Path(__file__).stem)
    iniciar_log(carpeta_corrida, Path(__file__).stem)

    filas = []
    for i, msg in enumerate(mailbox.mbox(str(args.mbox))):
        filas.append(mensaje_a_fila(msg, carpeta_adjuntos, f"sin-id-{i}"))
        if (i + 1) % 500 == 0:
            print(f"  {i + 1} correos leidos...")

    df = filas_a_tabla(filas)

    # Agrega sin borrar: si ya corriste sincronizar_gmail.py, esos correos se conservan.
    with sqlite3.connect(args.salida / "correos.sqlite") as con:
        try:
            existentes = {r[0] for r in con.execute("SELECT message_id FROM correos")}
        except sqlite3.OperationalError:  # la tabla aun no existe
            existentes = set()
        df[~df["message_id"].isin(existentes)].to_sql("correos", con, if_exists="append", index=False)

    por_remitente = generar_resumen(df, carpeta_corrida)

    n_adj = len(list(carpeta_adjuntos.iterdir()))
    print(f"\nListo: {len(df)} correos, {df['dominio'].nunique()} dominios, {n_adj} adjuntos PDF/XML.")
    print(f"Desde {df['fecha'].min()} hasta {df['fecha'].max()}")
    print("\nRemitentes principales:")
    print(por_remitente.head(10).to_string(index=False))
    print(f"\nBase de datos y adjuntos en: {args.salida.resolve()}")
    print(f"Resumen de esta corrida en: {carpeta_corrida.resolve()}")


if __name__ == "__main__":
    main()
