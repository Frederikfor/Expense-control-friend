"""
Pruebas del reporte de gastos (reporte.py) con movimientos inventados.
Correr desde la carpeta scripts:
    python -m pytest tests
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reporte import (POR_REVISAR, SIN_CATEGORIA, ErrorConfigReporte, cambio, cargar_config,  # noqa: E402
                     categorizar, colones, detectar_hormiga, detectar_suscripciones, generar_markdown, grupo_de,
                     marcar_transferencias_propias, metricas, nombre_comercio, preparar)

CONFIG = cargar_config()


def mov(fecha, monto, tipo="compra", direccion="salida", comercio=None, banco="BAC", moneda="CRC", ciudad=None):
    return {"banco": banco, "tipo": tipo, "direccion": direccion, "fecha": pd.Timestamp(fecha), "monto": monto,
            "moneda": moneda, "comercio": comercio, "ciudad": ciudad, "ultimos4": "4455",
            "autorizacion": "998877", "referencia": "528512345678", "remitente_verificado": 1}


def tabla(*filas):
    return pd.DataFrame(list(filas))


def test_categorias_por_palabra_y_por_tipo():
    assert categorizar("compra", "UBER EATS PENDING", CONFIG) == "Comida a domicilio"
    assert categorizar("compra", "UBER TRIP", CONFIG) == "Transporte por app"
    assert categorizar("compra", "Café Britt", CONFIG) == "Café y panadería"  # sin importar tildes
    assert categorizar("sinpe_enviado", None, CONFIG) == "Transferencias"
    assert categorizar("compra", "COMERCIO DESCONOCIDO", CONFIG) == SIN_CATEGORIA


def test_siglas_cortas_solo_como_palabra_completa():
    assert categorizar("compra", "UE COSTA RICA", CONFIG) == "Comida a domicilio"
    assert categorizar("compra", "BUENA VISTA", CONFIG) == SIN_CATEGORIA  # "UE" dentro de BUENA no cuenta


def test_orden_de_categorias_evita_choques():
    assert categorizar("compra", "DELTA 0061234567890", CONFIG) == "Viajes"  # aerolinea
    assert categorizar("compra", "DELTA CENTRO", CONFIG) == "Combustible"  # gasolinera
    assert categorizar("compra", "CARLS JR 123", CONFIG) == "Restaurantes y comida rápida"
    assert categorizar("compra", "PANADERIA CENTRO METROPOLITANO", CONFIG) == "Café y panadería"
    assert categorizar("compra", "GLOBAL VIA RUTA 27", CONFIG) == "Peajes, parqueo y transporte público"
    assert categorizar("compra", "BARBER SHOP CENTRAL", CONFIG) == "Cuidado personal"


def test_cada_categoria_tiene_un_grupo():
    assert grupo_de("Supermercado", CONFIG) == "Necesidades"
    assert grupo_de("Restaurantes y comida rápida", CONFIG) == "Gustos"
    assert grupo_de(SIN_CATEGORIA, CONFIG) == POR_REVISAR
    for cat in CONFIG["categorias"]:
        assert grupo_de(cat["nombre"], CONFIG) != POR_REVISAR, cat["nombre"]


def test_categoria_repetida_en_dos_grupos_es_error(tmp_path):
    texto = (Path(__file__).resolve().parents[2] / "config" / "reporte.toml").read_text(encoding="utf-8")
    ruta = tmp_path / "reporte.toml"
    ruta.write_text(texto.replace('"Iglesia y donaciones" = ["Iglesia y donaciones"]',
                                  '"Iglesia y donaciones" = ["Iglesia y donaciones", "Salud"]'), encoding="utf-8")
    try:
        cargar_config(ruta)
    except ErrorConfigReporte as error:
        assert "Salud" in str(error)
    else:
        raise AssertionError("se esperaba ErrorConfigReporte")


def test_prefijo_del_procesador_se_une_a_la_ciudad():
    assert nombre_comercio("DLC", "DIDI") == "DLC DIDI"
    assert nombre_comercio("AUTOMERCADO", "ESCAZU") == "AUTOMERCADO"


def test_transferencia_entre_cuentas_propias_no_es_gasto_ni_ingreso():
    df = tabla(mov("2026-10-03 09:45:52", 300000, "sinpe_enviado"),
               mov("2026-10-03 09:46:11", 300000, "credito_cuenta", "entrada", banco="Davibank"),
               mov("2026-10-03 12:00:00", 300000, "sinpe_enviado"))  # sin pareja: si es gasto
    marcado = marcar_transferencias_propias(df, 30)
    assert list(marcado["direccion"]) == ["interno", "interno", "salida"]


def test_devolucion_resta_y_dolares_se_convierten():
    df = preparar(tabla(mov("2026-09-05", 10, moneda="USD", comercio="AMAZON"),
                        mov("2026-09-06", 2000, "devolucion", "entrada", comercio="AMAZON")), CONFIG)
    assert list(df["monto_crc"]) == [10 * CONFIG["tipos_cambio"]["USD"], -2000]
    assert set(df["direccion"]) == {"salida"}


def test_hormiga_agrupa_compras_pequenas_por_categoria():
    df = preparar(tabla(*[mov(f"2026-09-{d:02d}", 3000, comercio="UBER") for d in range(1, 4)],
                        *[mov(f"2026-09-{d:02d}", 2500, comercio="DLC", ciudad="DIDI") for d in range(4, 6)],
                        mov("2026-09-10", 80000, comercio="UBER")), CONFIG)  # grande: no es hormiga
    h = detectar_hormiga(df, CONFIG)
    assert list(h["categoria"]) == ["Transporte por app"]
    assert (h["compras"].iloc[0], h["total"].iloc[0], h["principal"].iloc[0]) == (5, 14000, "UBER")


def test_suscripcion_requiere_meses_seguidos_y_monto_parecido():
    filas = [mov(f"2026-{m:02d}-15", 6865, comercio="AMAZON PRIME") for m in (7, 8, 9)]
    filas += [mov(f"2026-{m:02d}-15", monto, comercio="SUPER X") for m, monto in ((7, 5000), (8, 30000), (9, 9000))]
    filas += [mov("2026-07-15", 4000, comercio="NETFLIX"), mov("2026-09-15", 4000, comercio="NETFLIX")]
    s = detectar_suscripciones(preparar(tabla(*filas), CONFIG), "2026-09", CONFIG)
    assert list(s["comercio"]) == ["AMAZON PRIME"]


def test_mes_en_curso_compara_los_mismos_dias():
    df = preparar(tabla(mov("2026-09-02", 1000, comercio="UBER"), mov("2026-09-25", 50000, comercio="UBER"),
                        mov("2026-10-02", 3000, comercio="UBER")), CONFIG)
    m = metricas(df, "2026-10", datetime(2026, 10, 3, 12), CONFIG)
    assert (m["dias"], m["gasto"], m["gasto_prev"]) == (3, 3000, 1000)


def test_reporte_no_incluye_tarjetas_ni_autorizaciones_ni_referencias():
    df = preparar(tabla(mov("2026-09-02", 1000, comercio="UBER"), mov("2026-09-03", 5000, "sinpe_enviado")), CONFIG)
    texto = generar_markdown(metricas(df, "2026-09", datetime(2026, 10, 1), CONFIG), "100%", CONFIG, "prueba",
                             datetime(2026, 10, 1))
    assert "# Reporte 2026-09" in texto and "[[Reporte 2026-08]]" in texto
    assert "| Peajes, parqueo y transporte público |" not in texto and "| Gustos |" in texto
    for dato in ("4455", "998877", "528512345678"):
        assert dato not in texto


def test_formato_de_montos_y_cambios():
    assert colones(1234567.8) == "₡1.234.568"
    assert cambio(15000, 10000) == "+50%"
    assert cambio(5000, 0) == "nuevo"
