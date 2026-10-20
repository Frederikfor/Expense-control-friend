"""
Genera el reporte mensual de gastos en Markdown (para Obsidian) a partir de
la tabla "movimientos" de datos/correos.sqlite.

Uso (despues de procesar_correos.py):
    python reporte.py                      # reporte del mes actual hasta hoy
    python reporte.py --mes 2026-09        # un mes especifico
    python reporte.py --todos              # un reporte por cada mes con datos
    python reporte.py --todos --obsidian "C:\\ruta\\a\\boveda\\Control de gastos\\Reportes"

Genera "Reporte AAAA-MM.md" en una carpeta nueva de esta corrida (y en
--obsidian si se indica). Las reglas de categorias y los umbrales estan en
config/reporte.toml.

Privacidad: el reporte solo lleva totales, categorias y nombres de comercios.
Nunca incluye cuentas, tarjetas, autorizaciones, referencias ni la lista de
movimientos, y no muestra nombres de personas de las transferencias SINPE.
"""

import argparse
import calendar
import re
import sqlite3
import tomllib
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd

from leer_mbox import CARPETA_DATOS, carpeta_de_corrida, iniciar_log

RUTA_CONFIG = Path(__file__).resolve().parents[1] / "config" / "reporte.toml"
SIN_CATEGORIA = "Sin categoría"
TRANSFERENCIAS = "Transferencias"
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
CLAVES_REPORTE = ("ventana_transferencia_propia", "excluir_del_gasto", "umbral_hormiga", "min_compras_hormiga",
                  "meses_suscripcion", "variacion_suscripcion", "meses_tendencia")


class ErrorConfigReporte(ValueError):
    """config/reporte.toml tiene un dato faltante o invalido."""


def cargar_config(ruta=RUTA_CONFIG):
    ruta = Path(ruta)
    try:
        datos = tomllib.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ErrorConfigReporte(f"no se encontro {ruta}") from error
    except tomllib.TOMLDecodeError as error:
        raise ErrorConfigReporte(f"{ruta.name} tiene un error de formato: {error}") from error
    for clave in CLAVES_REPORTE:
        if clave not in datos.get("reporte", {}):
            raise ErrorConfigReporte(f"{ruta.name}: falta '{clave}' en [reporte]")
    for cat in datos.get("categorias", []):
        if not cat.get("nombre") or not cat.get("palabras"):
            raise ErrorConfigReporte(f"{ruta.name}: categoria sin 'nombre' o sin 'palabras': {cat}")
    if not datos.get("tipos_cambio", {}).get("USD"):
        raise ErrorConfigReporte(f"{ruta.name}: falta USD en [tipos_cambio]")
    datos.setdefault("categorias_por_tipo", {})
    return datos


def normalizar(texto):
    """Mayusculas, sin tildes y con espacios simples."""
    sin_tildes = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.upper().split())


def coincide(palabra, nombre):
    """Las palabras cortas (siglas como UE, JR, INS) tienen que aparecer completas."""
    p = normalizar(palabra)
    if len(p) <= 4:
        return f" {p} " in f" {re.sub(r'[^A-Z0-9&]+', ' ', nombre)} "
    return p in nombre


def nombre_comercio(comercio, ciudad):
    """Algunos vouchers parten 'DLC DIDI SAN JOSE' en comercio='DLC' y ciudad='DIDI'.
    Si el comercio es solo un prefijo corto del procesador de pagos, se le une la ciudad."""
    comercio = normalizar(comercio)
    if len(comercio) <= 3 and ciudad and not pd.isna(ciudad):
        return f"{comercio} {normalizar(ciudad)}".strip()
    return comercio or "(sin nombre)"


def categorizar(tipo, comercio, config):
    if tipo in config["categorias_por_tipo"]:
        return config["categorias_por_tipo"][tipo]
    nombre = normalizar(comercio)
    for cat in config["categorias"]:
        if any(coincide(p, nombre) for p in cat["palabras"]):
            return cat["nombre"]
    return SIN_CATEGORIA


def en_colones(monto, moneda, config):
    if moneda in (None, "CRC") or pd.isna(moneda):
        return monto
    tc = config["tipos_cambio"].get(moneda)
    if tc is None:
        print(f"  Aviso: no hay tipo de cambio para {moneda} en config/reporte.toml; se cuenta como 0.")
        config["tipos_cambio"][moneda] = 0.0
        return 0.0
    return monto * tc


def marcar_transferencias_propias(df, minutos):
    """Una salida y una entrada del mismo monto y moneda en bancos distintos, con pocos minutos
    de diferencia, son dinero que pasa entre cuentas propias: se marcan como 'interno'."""
    df = df.copy()
    salidas = df[(df["direccion"] == "salida") & (df["tipo"] != "compra")].sort_values("fecha")
    entradas = df[df["direccion"] == "entrada"]
    usadas = set()
    for i, s in salidas.iterrows():
        candidatas = entradas[(entradas["monto"] == s["monto"]) & (entradas["moneda"] == s["moneda"])
                              & (entradas["banco"] != s["banco"]) & ~entradas.index.isin(usadas)
                              & ((entradas["fecha"] - s["fecha"]).abs() <= pd.Timedelta(minutes=minutos))]
        if not candidatas.empty:
            j = (candidatas["fecha"] - s["fecha"]).abs().idxmin()
            usadas.add(j)
            df.loc[[i, j], "direccion"] = "interno"
    return df


def preparar(movimientos, config):
    """Deja solo entradas y salidas, con monto en colones y categoria.
    Las devoluciones quedan como gasto negativo, para que resten de su categoria."""
    df = movimientos[movimientos["direccion"].isin(["salida", "entrada"])].copy()
    df = df[df["monto"].notna()]
    df["fecha"] = pd.to_datetime(df["fecha"])
    df = marcar_transferencias_propias(df, config["reporte"]["ventana_transferencia_propia"])
    df = df[df["direccion"] != "interno"].copy()
    df["mes"] = df["fecha"].dt.strftime("%Y-%m")
    df["monto_crc"] = [en_colones(m, mo, config) for m, mo in zip(df["monto"], df["moneda"])]
    devolucion = df["tipo"] == "devolucion"
    df.loc[devolucion, "direccion"] = "salida"
    df.loc[devolucion, "monto_crc"] *= -1
    ciudades = df["ciudad"] if "ciudad" in df else pd.Series(None, index=df.index)
    df["comercio_norm"] = [nombre_comercio(c, ci) for c, ci in zip(df["comercio"], ciudades)]
    df["categoria"] = [categorizar(t, c, config) for t, c in zip(df["tipo"], df["comercio_norm"])]
    return df


def periodo(mes, hoy):
    """(inicio, fin, dias_transcurridos, dias_del_mes) del mes; si es el mes en curso, corta en hoy."""
    inicio = datetime.strptime(mes, "%Y-%m")
    dias_mes = calendar.monthrange(inicio.year, inicio.month)[1]
    en_curso = (inicio.year, inicio.month) == (hoy.year, hoy.month)
    dias = hoy.day if en_curso else dias_mes
    fin = inicio + pd.Timedelta(days=dias)
    return inicio, fin, dias, dias_mes


def mes_anterior(mes):
    return (datetime.strptime(mes, "%Y-%m") - pd.DateOffset(months=1)).strftime("%Y-%m")


def recortar(df, mes, dias):
    """Movimientos del mes hasta el dia 'dias' (para comparar periodos iguales)."""
    inicio = datetime.strptime(mes, "%Y-%m")
    dias = min(dias, calendar.monthrange(inicio.year, inicio.month)[1])
    return df[(df["fecha"] >= inicio) & (df["fecha"] < inicio + pd.Timedelta(days=dias))]


def detectar_hormiga(gastos, config):
    """Compras pequenas que se repiten en la misma categoria (ej. viajes cortos de Uber y DiDi juntos)."""
    r = config["reporte"]
    chicas = gastos[(gastos["tipo"] == "compra") & (gastos["monto_crc"] <= r["umbral_hormiga"])
                    & (gastos["categoria"] != SIN_CATEGORIA)]
    if chicas.empty:
        return pd.DataFrame(columns=["categoria", "compras", "total", "principal"])
    grupos = chicas.groupby("categoria")["monto_crc"].agg(compras="size", total="sum")
    grupos["principal"] = chicas.groupby("categoria")["comercio_norm"].agg(lambda c: c.value_counts().index[0])
    grupos = grupos[grupos["compras"] >= r["min_compras_hormiga"]].sort_values("total", ascending=False)
    return grupos.reset_index()


def detectar_suscripciones(gastos_hasta_mes, mes, config):
    """Mismo comercio con un cobro casi igual en los ultimos N meses seguidos, terminando en 'mes'."""
    r = config["reporte"]
    meses = [mes]
    for _ in range(r["meses_suscripcion"] - 1):
        meses.insert(0, mes_anterior(meses[0]))
    compras = gastos_hasta_mes[(gastos_hasta_mes["tipo"] == "compra") & gastos_hasta_mes["mes"].isin(meses)]
    por_mes = compras.groupby(["comercio_norm", "mes"])["monto_crc"].agg(cobros="size", total="sum").reset_index()
    filas = []
    for comercio, g in por_mes.groupby("comercio_norm"):
        if len(g) < len(meses) or g["cobros"].max() > 2:
            continue
        media = g["total"].mean()
        if media > 0 and g["total"].std(ddof=0) / media <= r["variacion_suscripcion"]:
            filas.append({"comercio": comercio, "monto_mensual": g["total"].median()})
    return pd.DataFrame(filas, columns=["comercio", "monto_mensual"]).sort_values("monto_mensual", ascending=False)


def franja(hora):
    if 5 <= hora < 12:
        return "la mañana"
    if 12 <= hora < 18:
        return "la tarde"
    if 18 <= hora < 23:
        return "la noche"
    return "la madrugada"


def habitos(gastos):
    """Hechos calculados del mes, en frases cortas."""
    consumo = gastos[gastos["categoria"] != TRANSFERENCIAS]
    compras = consumo[consumo["tipo"] == "compra"]
    if consumo.empty:
        return []
    frases = []
    por_dia = consumo.groupby(consumo["fecha"].dt.dayofweek)["monto_crc"].sum()
    frases.append(f"El día con más gasto es el **{DIAS[int(por_dia.idxmax())]}** ({colones(por_dia.max())}).")
    finde = consumo[consumo["fecha"].dt.dayofweek >= 5]["monto_crc"].sum() / consumo["monto_crc"].sum()
    frases.append(f"El fin de semana concentra el **{finde:.0%}** del gasto (2 de 7 días son el 29 %).")
    if not compras.empty:
        frases.append(f"{len(compras)} compras con tarjeta, con un ticket promedio de "
                      f"**{colones(compras['monto_crc'].mean())}**.")
        con_hora = compras[(compras["fecha"].dt.hour != 0) | (compras["fecha"].dt.minute != 0)]
        if len(con_hora) >= 5:
            mas = con_hora["fecha"].dt.hour.map(franja).value_counts()
            frases.append(f"La mayoría de las compras ocurre en **{mas.index[0]}** ({mas.iloc[0] / len(con_hora):.0%}).")
    top = consumo.groupby("comercio_norm")["monto_crc"].sum().nlargest(3)
    if not top.empty:
        frases.append("Comercios donde más se gastó: " + ", ".join(f"{c.title()} ({colones(v)})" for c, v in top.items()) + ".")
    return frases


def recomendaciones(m):
    """Reglas simples sobre las metricas del mes. El analisis mas fino lo hara el modelo local."""
    rec = []
    if m["hormiga_total"] > 0 and m["gasto"] > 0:
        rec.append(f"Las compras hormiga suman {colones(m['hormiga_total'])} ({m['hormiga_total'] / m['gasto']:.0%} "
                   f"del gasto). Bajarlas a la mitad ahorraría unos {colones(m['hormiga_mensual'] / 2 * 12)} al año.")
    for cat, (antes, ahora) in m["cambios_categoria"].items():
        if cat != SIN_CATEGORIA and antes >= 10000 and ahora - antes >= 20000 and ahora / antes >= 1.25:
            rec.append(f"**{cat}** subió {ahora / antes - 1:.0%} frente al mismo periodo del mes anterior "
                       f"({colones(antes)} → {colones(ahora)}).")
    if not m["suscripciones"].empty:
        total = m["suscripciones"]["monto_mensual"].sum()
        rec.append(f"Revisar {len(m['suscripciones'])} cobros recurrentes por {colones(total)} al mes "
                   f"({colones(total * 12)} al año) y cancelar los que no se usen.")
    if m["sin_categoria"] >= 0.2:
        rec.append(f"El {m['sin_categoria']:.0%} del gasto no tiene categoría: agregar palabras en config/reporte.toml.")
    return rec[:4]


def colones(valor):
    return "₡" + f"{valor:,.0f}".replace(",", ".")


def cambio(ahora, antes):
    if not antes or abs(antes) < 1000:
        return "nuevo" if ahora else "—"
    return f"{ahora / antes - 1:+.0%}"


def metricas(df, mes, hoy, config):
    _, _, dias, dias_mes = periodo(mes, hoy)
    actual = recortar(df, mes, dias)
    previo = recortar(df, mes_anterior(mes), dias)
    excluir = config["reporte"]["excluir_del_gasto"]

    def separar(datos):
        salidas = datos[datos["direccion"] == "salida"]
        return salidas[~salidas["categoria"].isin(excluir)], salidas[salidas["categoria"].isin(excluir)]

    (gastos, aparte), (gastos_prev, aparte_prev) = separar(actual), separar(previo)
    hormiga = detectar_hormiga(gastos, config)
    por_cat = gastos.groupby("categoria")["monto_crc"].sum().sort_values(ascending=False)
    por_cat_prev = gastos_prev.groupby("categoria")["monto_crc"].sum()
    gasto = gastos["monto_crc"].sum()
    hasta_mes = df[(df["direccion"] == "salida") & (df["mes"] <= mes) & ~df["categoria"].isin(excluir)]

    def por_tipo(datos, tipos):
        return datos[datos["tipo"].isin(tipos)]["monto_crc"].sum()

    return {
        "compras": por_tipo(gastos, ["compra", "devolucion"]), "compras_prev": por_tipo(gastos_prev, ["compra", "devolucion"]),
        "efectivo": por_tipo(gastos, ["retiro_efectivo"]), "efectivo_prev": por_tipo(gastos_prev, ["retiro_efectivo"]),
        "transferencias": por_tipo(gastos, ["sinpe_enviado"]), "transferencias_prev": por_tipo(gastos_prev, ["sinpe_enviado"]),
        "aparte": aparte["monto_crc"].sum(), "aparte_prev": aparte_prev["monto_crc"].sum(),
        "mes": mes, "dias": dias, "dias_mes": dias_mes,
        "gasto": gasto, "gasto_prev": gastos_prev["monto_crc"].sum(),
        "ingresos": actual[actual["direccion"] == "entrada"]["monto_crc"].sum(),
        "ingresos_prev": previo[previo["direccion"] == "entrada"]["monto_crc"].sum(),
        "proyeccion": gasto / dias * dias_mes if dias else 0,
        "hormiga": hormiga, "hormiga_total": hormiga["total"].sum(),
        "hormiga_prev": detectar_hormiga(gastos_prev, config)["total"].sum(),
        "hormiga_mensual": hormiga["total"].sum() / dias * dias_mes if dias else 0,
        "por_categoria": por_cat,
        "cambios_categoria": {c: (por_cat_prev.get(c, 0), v) for c, v in por_cat.items()},
        "sin_categoria": por_cat.get(SIN_CATEGORIA, 0) / gasto if gasto else 0,
        "suscripciones": detectar_suscripciones(hasta_mes, mes, config),
        "tendencia": hasta_mes.groupby("mes")["monto_crc"].sum().tail(config["reporte"]["meses_tendencia"]),
        "habitos": habitos(gastos),
        "sin_verificar": int((actual.get("remitente_verificado") == 0).sum()) if "remitente_verificado" in actual else 0,
        "movimientos": len(actual),
    }


def generar_markdown(m, calidad, config, fuente, hoy, corte=None):
    r = config["reporte"]
    mes, prev = m["mes"], mes_anterior(m["mes"])
    en_curso = m["dias"] < m["dias_mes"]
    titulo_periodo = f"del 1 al {m['dias']}" if en_curso else "completo"
    l = [
        "---", "tipo: reporte", f"periodo: {mes}", f"actualizado: {hoy:%Y-%m-%d}", f"fuente: {fuente}",
        "tipos_de_cambio: " + ", ".join(f"{k}={v:g}" for k, v in config["tipos_cambio"].items()), "---",
        f"# Reporte {mes}", "",
        f"[[Control de gastos]] · Anterior: [[Reporte {prev}]]", "",
        *([f"> [!warning] Datos hasta el {corte:%d/%m/%Y}. Correr `actualizar.bat` para incluir lo más reciente.", ""]
          if en_curso and corte and (hoy - corte).days >= 2 else []),
        f"> Mes {titulo_periodo}. Se compara con los mismos días de {prev}. Montos en colones; "
        f"los dólares se convierten a ₡{config['tipos_cambio']['USD']:g}. Las transferencias entre cuentas "
        f"propias no se cuentan.", "",
        "## Resumen",
        "| | Este mes | Mes anterior | Cambio |", "|---|---|---|---|",
        f"| **Gasto total** | **{colones(m['gasto'])}** | {colones(m['gasto_prev'])} | {cambio(m['gasto'], m['gasto_prev'])} |",
        *[f"| · {nombre} | {colones(m[k])} | {colones(m[k + '_prev'])} | {cambio(m[k], m[k + '_prev'])} |"
          for nombre, k in (("Compras con tarjeta", "compras"), ("Transferencias a terceros", "transferencias"),
                            ("Efectivo", "efectivo"))],
        f"| Ingresos | {colones(m['ingresos'])} | {colones(m['ingresos_prev'])} | {cambio(m['ingresos'], m['ingresos_prev'])} |",
        f"| Gasto hormiga | {colones(m['hormiga_total'])} | {colones(m['hormiga_prev'])} | "
        f"{cambio(m['hormiga_total'], m['hormiga_prev'])} |",
    ]
    if m["aparte"] or m["aparte_prev"]:
        l.append(f"| Débitos sin detalle (no sumados) | {colones(m['aparte'])} | {colones(m['aparte_prev'])} | "
                 f"{cambio(m['aparte'], m['aparte_prev'])} |")
    if en_curso and m["dias"] >= 7:  # con menos dias la proyeccion no dice nada
        l.append(f"| Proyección al cierre | {colones(m['proyeccion'])} | | |")
    l += ["", "## Gasto por categoría"]
    cats = m["por_categoria"]
    if cats.empty:
        l.append("Sin gastos en el periodo.")
    else:
        top = cats.head(6)
        resto = cats.iloc[6:].sum()
        l += ["```mermaid", "pie showData", "    title Gasto por categoría (₡)"]
        l += [f'    "{c}" : {v:.0f}' for c, v in top.items()]
        if resto > 0:
            l.append(f'    "Otros" : {resto:.0f}')
        l += ["```", "", "| Categoría | Gasto | % | Cambio |", "|---|---|---|---|"]
        for c, v in cats.items():
            antes = m["cambios_categoria"][c][0]
            l.append(f"| {c} | {colones(v)} | {v / m['gasto']:.0%} | {cambio(v, antes)} |")
    if len(m["tendencia"]) >= 2:
        meses = ", ".join(f'"{x}"' for x in m["tendencia"].index)
        valores = ", ".join(f"{v / 1000:.0f}" for v in m["tendencia"].values)
        l += ["", "## Tendencia", "```mermaid", "xychart-beta", '    title "Gasto por mes (miles de ₡)"',
              f"    x-axis [{meses}]", f"    bar [{valores}]", "```"]
        if en_curso:
            l.append(f"_El último mes va al día {m['dias']}._")
    l += ["", "## Gastos hormiga",
          f"_Compras de hasta {colones(r['umbral_hormiga'])} que se repiten {r['min_compras_hormiga']} veces o más "
          f"en la misma categoría. \"Al año\" supone el mismo ritmo durante 12 meses._", ""]
    if m["hormiga"].empty:
        l.append("No se detectaron este mes.")
    else:
        l += ["| Categoría | Compras | Total | Al año | Más frecuente |", "|---|---|---|---|---|"]
        factor = 12 * m["dias_mes"] / m["dias"]
        for f in m["hormiga"].head(5).itertuples():
            l.append(f"| {f.categoria} | {f.compras} | {colones(f.total)} | {colones(f.total * factor)} | "
                     f"{f.principal.title()} |")
    l += ["", "## Cobros recurrentes"]
    if m["suscripciones"].empty:
        l.append(f"No se detectaron cobros iguales en los últimos {r['meses_suscripcion']} meses.")
    else:
        l += ["| Comercio | Monto mensual | ¿Mantener? |", "|---|---|---|"]
        l += [f"| {f.comercio.title()} | {colones(f.monto_mensual)} | |" for f in m["suscripciones"].itertuples()]
    l += ["", "## Hábitos observados"] + [f"- {h}" for h in m["habitos"] or ["Sin datos suficientes."]]
    rec = recomendaciones(m)
    l += ["", "## Recomendaciones"] + [f"{i}. {x}" for i, x in enumerate(rec or ["Nada que señalar este mes."], 1)]
    l += ["", "## Calidad de los datos",
          f"- Correos bancarios entendidos: {calidad}",
          f"- Movimientos del periodo: {m['movimientos']}",
          f"- Movimientos de correos sin verificar (DKIM/SPF): {m['sin_verificar']}",
          f"- Gasto sin categoría: {m['sin_categoria']:.0%}",
          "- Davibank no envía alertas de compras con tarjeta: sus gastos faltan hasta leer los estados de cuenta.",
          ""]
    return "\n".join(l)


def calidad_correos(con):
    try:
        cla = pd.read_sql("SELECT estado FROM clasificacion", con)
    except Exception:
        return "sin datos"
    if cla.empty:
        return "sin datos"
    malos = cla["estado"].isin(["no_reconocido", "error"]).sum()
    return f"{1 - malos / len(cla):.1%} ({len(cla) - malos} de {len(cla)})"


def fuente_de_datos(salida):
    corridas = sorted(Path(salida).glob("procesar_correos_*"))
    return corridas[-1].name if corridas else "correos.sqlite"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--salida", type=Path, default=CARPETA_DATOS, help="carpeta con correos.sqlite")
    p.add_argument("--mes", help="mes AAAA-MM (por defecto, el mes actual)")
    p.add_argument("--todos", action="store_true", help="un reporte por cada mes con datos")
    p.add_argument("--obsidian", type=Path, help="carpeta de la boveda donde copiar los reportes")
    args = p.parse_args()

    config = cargar_config()
    carpeta_corrida = carpeta_de_corrida(args.salida, Path(__file__).stem)
    iniciar_log(carpeta_corrida, Path(__file__).stem)
    hoy = datetime.now()

    with sqlite3.connect(args.salida / "correos.sqlite") as con:
        movimientos = pd.read_sql("SELECT * FROM movimientos", con)
        calidad = calidad_correos(con)
    df = preparar(movimientos, config)
    df = df[df["fecha"] <= hoy]
    if df.empty:
        print("No hay movimientos. Corre primero procesar_correos.py.")
        return
    # Si los correos no estan al dia, el mes en curso se corta en el ultimo dato y no en hoy,
    # para no comparar dias sin datos contra dias con datos.
    corte = min(hoy, df["fecha"].max().to_pydatetime())
    if (hoy - corte).days >= 2:
        print(f"Aviso: el ultimo movimiento es del {corte:%Y-%m-%d}. Corre actualizar.bat para traer lo nuevo.")

    meses = sorted(df["mes"].unique()) if args.todos else [args.mes or hoy.strftime("%Y-%m")]
    destinos = [carpeta_corrida] + ([args.obsidian] if args.obsidian else [])
    fuente = fuente_de_datos(args.salida)
    for mes in meses:
        m = metricas(df, mes, corte, config)
        texto = generar_markdown(m, calidad, config, fuente, hoy, corte)
        for carpeta in destinos:
            carpeta.mkdir(parents=True, exist_ok=True)
            (carpeta / f"Reporte {mes}.md").write_text(texto, encoding="utf-8")
        print(f"Reporte {mes}: gasto {colones(m['gasto'])}, hormiga {colones(m['hormiga_total'])}, "
              f"{len(m['suscripciones'])} cobros recurrentes.")
    print(f"\nReportes en: {carpeta_corrida.resolve()}")
    if args.obsidian:
        print(f"Copiados a Obsidian: {args.obsidian.resolve()}")


if __name__ == "__main__":
    main()
