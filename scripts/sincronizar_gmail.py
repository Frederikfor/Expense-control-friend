"""
Lee los correos de bancos directo de Gmail (solo lectura) y los agrega a
datos/correos.sqlite, la misma tabla que crea leer_mbox.py.

Instalar una vez (en tu entorno virtual):
    pip install pandas openpyxl google-api-python-client google-auth-oauthlib keyring

Uso normal (trae lo nuevo desde la ultima vez):
    python sincronizar_gmail.py --credenciales "C:\\ruta\\client_secret.json" --salida "C:\\ruta\\datos"

Recuperar historia de un banco (ej. el que quedo con un solo mes):
    python sincronizar_gmail.py --credenciales ... --salida ... --consulta "from:alertas@banco.example" --desde 2024-10-01

La primera vez se abre el navegador para que autorices. El permiso que se
guarda queda en el Administrador de credenciales de Windows, no en un archivo.
Los correos repetidos (por ejemplo, los que ya venian en el .mbox) se ignoran.
"""

import argparse
import base64
import email
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import keyring
import pandas as pd
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from extractores import DOMINIOS_BANCO
from leer_mbox import CARPETA_DATOS, carpeta_de_corrida, filas_a_tabla, generar_resumen, iniciar_log, mensaje_a_fila

PERMISOS = ["https://www.googleapis.com/auth/gmail.readonly"]
SERVICIO_KEYRING = "gastos-gmail"


def obtener_credenciales(ruta_client_secret, reautorizar=False):
    datos_cliente = json.loads(Path(ruta_client_secret).read_text())
    cliente = datos_cliente.get("installed") or datos_cliente.get("web")
    refresh = None if reautorizar else keyring.get_password(SERVICIO_KEYRING, cliente["client_id"])
    if refresh:
        cred = Credentials(None, refresh_token=refresh, token_uri=cliente["token_uri"],
                           client_id=cliente["client_id"], client_secret=cliente["client_secret"],
                           scopes=PERMISOS)
        try:
            cred.refresh(Request())
            return cred
        except RefreshError:
            # Pasa si revocaste el acceso o si la app de Google Cloud esta en modo "Prueba"
            # (ahi el permiso vence a los 7 dias). Se vuelve a pedir en el navegador.
            print("El permiso de Gmail vencio o fue revocado; se abre el navegador para autorizar de nuevo.")
    flujo = InstalledAppFlow.from_client_secrets_file(str(ruta_client_secret), PERMISOS)
    cred = flujo.run_local_server(port=0)
    # Solo se guarda el refresh token (corto) en el Administrador de credenciales.
    keyring.set_password(SERVICIO_KEYRING, cliente["client_id"], cred.refresh_token)
    return cred


def preparar_base(con):
    con.execute("CREATE TABLE IF NOT EXISTS gmail_vistos (gmail_id TEXT PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS estado (clave TEXT PRIMARY KEY, valor TEXT)")


def leer_estado(con, clave):
    fila = con.execute("SELECT valor FROM estado WHERE clave = ?", (clave,)).fetchone()
    return fila[0] if fila else None


def guardar_estado(con, clave, valor):
    con.execute("INSERT OR REPLACE INTO estado (clave, valor) VALUES (?, ?)", (clave, valor))


def ids_existentes(con, tabla, columna):
    try:
        return {r[0] for r in con.execute(f"SELECT {columna} FROM {tabla}")}
    except sqlite3.OperationalError:  # la tabla aun no existe
        return set()


def guardar_lote(con, filas, ids_gmail):
    """Guarda un lote en la base (sin repetir message_id) y devuelve cuantos correos agrego."""
    agregados = 0
    if filas:
        df = filas_a_tabla(filas)
        df = df[~df["message_id"].isin(ids_existentes(con, "correos", "message_id"))]
        df.to_sql("correos", con, if_exists="append", index=False)
        agregados = len(df)
    con.executemany("INSERT OR IGNORE INTO gmail_vistos (gmail_id) VALUES (?)", [(g,) for g in ids_gmail])
    con.commit()
    return agregados


def listar_ids(servicio, consulta):
    ids, token = [], None
    while True:
        resp = servicio.users().messages().list(userId="me", q=consulta, pageToken=token, maxResults=500).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        token = resp.get("nextPageToken")
        if not token:
            return ids


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--credenciales", type=Path, required=True, help="JSON de OAuth descargado de Google Cloud")
    p.add_argument("--salida", type=Path, default=CARPETA_DATOS, help="carpeta con correos.sqlite")
    p.add_argument("--etiqueta", default="banco", help="etiqueta de Gmail a leer (por defecto: banco)")
    p.add_argument("--consulta", help="busqueda de Gmail en lugar de la etiqueta, ej. 'from:alertas@banco.com'")
    p.add_argument("--desde", type=date.fromisoformat, help="fecha inicial AAAA-MM-DD (por defecto: ultima sincronizacion)")
    p.add_argument("--reautorizar", action="store_true", help="volver a pedir permiso en el navegador")
    args = p.parse_args()

    args.salida.mkdir(parents=True, exist_ok=True)
    carpeta_adjuntos = args.salida / "adjuntos"
    carpeta_adjuntos.mkdir(exist_ok=True)
    carpeta_corrida = carpeta_de_corrida(args.salida, Path(__file__).stem)
    iniciar_log(carpeta_corrida, Path(__file__).stem)
    con = sqlite3.connect(args.salida / "correos.sqlite")
    preparar_base(con)

    # Desde cuando buscar: lo que diga --desde, o la ultima sincronizacion menos
    # 2 dias de margen, o 2 anos atras si es la primera vez.
    ultima = leer_estado(con, "ultima_sincronizacion")
    if args.desde:
        desde = args.desde
    elif ultima:
        desde = date.fromisoformat(ultima) - timedelta(days=2)
    else:
        desde = date.today() - timedelta(days=730)
    # Etiqueta O remitentes oficiales: si un banco estrena un remitente que tu filtro
    # de Gmail no etiqueta, igual se descarga (mientras sea del dominio del banco).
    dominios = " OR ".join(sorted(DOMINIOS_BANCO))
    base = args.consulta or f'(label:"{args.etiqueta}" OR from:({dominios}))'
    consulta = f"{base} after:{desde:%Y/%m/%d}"
    print(f"Buscando en Gmail: {consulta}")

    servicio = build("gmail", "v1", credentials=obtener_credenciales(args.credenciales, args.reautorizar),
                     cache_discovery=False)
    vistos = ids_existentes(con, "gmail_vistos", "gmail_id")
    nuevos = [i for i in listar_ids(servicio, consulta) if i not in vistos]
    print(f"{len(nuevos)} correos nuevos por descargar.")

    agregados, descargados = 0, 0
    filas, ids_lote = [], []
    try:
        for n, gid in enumerate(nuevos, 1):
            # num_retries: si Gmail responde "cuota excedida" (403/429), espera y reintenta solo.
            resp = servicio.users().messages().get(userId="me", id=gid, format="raw").execute(num_retries=8)
            msg = email.message_from_bytes(base64.urlsafe_b64decode(resp["raw"]))
            fila = mensaje_a_fila(msg, carpeta_adjuntos, f"gmail-{gid}")
            fila["etiquetas_gmail"] = ",".join(resp.get("labelIds", []))
            filas.append(fila)
            ids_lote.append(gid)
            if n % 50 == 0:
                agregados += guardar_lote(con, filas, ids_lote)
                descargados += len(ids_lote)
                filas, ids_lote = [], []
                print(f"  {n} descargados...")
    finally:
        # Si algo falla a medias, lo ya descargado queda guardado y la proxima corrida sigue desde ahi.
        agregados += guardar_lote(con, filas, ids_lote)
        descargados += len(ids_lote)
    if descargados == len(nuevos) and not args.consulta and not args.desde:
        guardar_estado(con, "ultima_sincronizacion", datetime.now().date().isoformat())
    con.commit()

    todo = pd.read_sql("SELECT * FROM correos", con, parse_dates=["fecha"])
    con.close()
    print(f"\nListo: {agregados} correos agregados ({len(nuevos) - agregados} ya estaban). "
          f"Total en la base: {len(todo)}.")
    if not todo.empty:
        generar_resumen(todo, carpeta_corrida)
        print(f"Resumen de esta corrida en: {carpeta_corrida / 'resumen.xlsx'}")


if __name__ == "__main__":
    main()
