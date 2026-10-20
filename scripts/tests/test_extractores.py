"""
Pruebas de los extractores con correos basados en las muestras reales
(los numeros son inventados). Correr desde la carpeta scripts:
    pip install pytest
    python -m pytest tests
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from extractores import extraer, fecha_numerica, parse_monto  # noqa: E402

BAC_COMPRA_VIEJO = """BAC
 Hola NOMBRE APELLIDO
 A continuación le detallamos la transacción realizada:
 Comercio:
 UBER
 Ciudad y país:
 , Estados Unidos
 Fecha:
 Sep 14, 2025, 18:42
 AMEX:
 ***********1234
 Autorización:
 482913
 Referencia:
 Tipo de Transacción:
 COMPRA
 Monto:
 USD 23.47
 ¿Tiene dudas sobre esta transacción?"""

BAC_COMPRA_NUEVO_PARTIDO = """Hola
NOMBRE
 A continuación le detallamos la
transacción
realizada:
 Comercio:
MOBILE SUICA APPLE
 Ciudad y país:
0312345678, Japón
 Fecha:
 Oct 21, 2025, 09:05
VISA
************5678
 Autorización:
123456
 Referencia:
512345678901
 Tipo de Transacción:
COMPRA
 Monto:
JPY .50
 ¿Tiene
dudas sobre esta transacción?"""

BAC_COMPRA_CRC = """ Comercio:
 DICA DESAROLLOS
 Ciudad y país:
 ALAJUELA, Costa Rica
 Fecha:
 Abr 3, 2025, 13:10
 VISA
 ************5678
 Autorización:
 654321
 Referencia:
 509312345678
 Tipo de Transacción:
 COMPRA
 Monto:
 CRC 4,350.00"""

BAC_SINPE = """BAC San José
 Notificación de Transferencia SINPE
 Hola: NOMBRE :
 BAC le comunica que la transferencia SINPE en tiempo real con el número de referencia 2025091415283700012345678, debitando su cuenta IBAN CR0510XXXXXXXXXXXX4321 por un monto de 125,000.00 Colones. Día y hora: 14/09/2025 03:28:37 p.m. fue aplicada correctamente."""

BN_VOUCHER_1 = """Estimado señor(a): NOMBRE
Reciba un cordial saludo de parte del Banco Nacional.
Por este medio le hacemos llegar el comprobante de Comprarealizada en APPLE.COM BILL         CUPERTINO     USA el 9 de Octubre de 2025a las 11:15 p.m.
APPLE.COM BILL         CUPERTINO     USA
Oct 9, 2025 - 11:15 p.m.
MASTERCARD************4455
NRO. AUT:
112233
REF:
528212345678
TOTAL:
CRC 1990,00
Estimado cliente, esta notificación es generada"""

BN_VOUCHER_2 = """Estimado señor(a): *NOMBRE*
Reciba un cordial saludo de parte del Banco Nacional.
Por este medio le hacemos llegar el comprobante de *COMPRA* realizada en *BEST BUY 0001234       SCHAUMBURG    USA* el *12 de Octubre de 2025* a las *16:20*
BEST BUY 0001234       SCHAUMBURG    USA
Oct 12, 2025 - 16:20 MASTERCARD ************4455 NRO. AUT: 998877 REF: 528512345678 TOTAL: USD 1,249.99
Estimado cliente"""

BN_SOBRE = """*Estimado (a): NOMBRE*
El Banco Nacional le comunica que se ha realizado el aporte al Sobre N° 20012345678 - Caja chica a nombre de NOMBRE a través de Rebajo automático realizado el día 15/08/2025 a las 06:00:12 horas, comprobante 12345678           por un monto de 25,000.00 colones."""

DAVI_DEBITO = """En el marco del proceso de integración ...
 Estimado(a) Cliente:
Se realizó un débito a la cuenta bancaria en CRC terminada en *******7788 , por un monto de CRC 15,750.00 . La transacción se realizó el día 23-09-2025 14:05:33
En cumplimiento con la legislación local vigente"""

DAVI_CUOTAS = """ Notificación de solicitud de Conversión de Compra a cuotas
 Recibimos su solicitud de convertir a cuotas la compra realizada el 05/07/2025 10:11:12. Dicha gestión se hizo por medio de la Banca Móvil.
 Detalles de la transacción: Nombre del comercio:COPA AIR 2301234567890 Monto de la compra: $845.30 Cantidad de cuotas: 6 Tasa de interés efectiva anual: 0% Comisión: 2.50%"""


def test_parse_monto():
    assert parse_monto("125,000.00") == 125000.0
    assert parse_monto("1990,00") == 1990.0
    assert parse_monto("12.500,00") == 12500.0
    assert parse_monto("1,249.99") == 1249.99
    assert parse_monto(".50") == 0.5
    assert parse_monto("845300") == 845300.0


def test_fecha_ambigua_usa_la_mas_cercana_al_correo():
    correo = datetime(2025, 3, 4, 12, 0)
    assert fecha_numerica("04", "03", "2025", referencia=correo) == datetime(2025, 3, 4)
    assert fecha_numerica("03", "04", "2025", referencia=correo) == datetime(2025, 3, 4)
    assert fecha_numerica("25", "03", "2025") == datetime(2025, 3, 25)


@pytest.mark.parametrize("remitente", ["notificacionbac@baccredomatic.cr"])
def test_bac_compra_formato_viejo(remitente):
    r = extraer(remitente, "Notificación de transacción UBER 14-09-2025 - 18:42", BAC_COMPRA_VIEJO)
    m = r.movimiento
    assert (r.estado, m.tipo, m.direccion) == ("movimiento", "compra", "salida")
    assert (m.monto, m.moneda, m.comercio) == (23.47, "USD", "UBER")
    assert (m.pais, m.ciudad, m.marca_tarjeta, m.ultimos4) == ("Estados Unidos", None, "AMEX", "1234")
    assert m.fecha == datetime(2025, 9, 14, 18, 42)
    assert m.referencia is None and m.autorizacion == "482913"


def test_bac_compra_formato_nuevo_con_lineas_partidas():
    r = extraer("notificacion@notificacionesbaccr.com", "Notificación de transacción MOBILE SUICA APPLE", BAC_COMPRA_NUEVO_PARTIDO)
    m = r.movimiento
    assert (m.monto, m.moneda, m.comercio, m.pais, m.marca_tarjeta) == (0.5, "JPY", "MOBILE SUICA APPLE", "Japón", "VISA")
    assert m.fecha == datetime(2025, 10, 21, 9, 5)


def test_bac_compra_colones_mes_en_espanol():
    m = extraer("notificacion@notificacionesbaccr.com", "Notificación de transacción DICA", BAC_COMPRA_CRC).movimiento
    assert (m.monto, m.moneda, m.ciudad, m.pais) == (4350.0, "CRC", "ALAJUELA", "Costa Rica")
    assert m.fecha == datetime(2025, 4, 3, 13, 10)


def test_bac_sinpe_enviado():
    m = extraer("notificaciones@baccredomatic.cr", "Notificación de Transferencia", BAC_SINPE,
                datetime(2025, 9, 14, 15, 29)).movimiento
    assert (m.tipo, m.direccion, m.monto, m.moneda, m.ultimos4) == ("sinpe_enviado", "salida", 125000.0, "CRC", "4321")
    assert m.fecha == datetime(2025, 9, 14, 15, 28, 37)


def test_bncr_voucher_coma_decimal_y_pm():
    m = extraer("bncontacto@bncr.fi.cr", "Voucher Digital", BN_VOUCHER_1).movimiento
    assert (m.tipo, m.monto, m.moneda) == ("compra", 1990.0, "CRC")
    assert (m.comercio, m.ciudad, m.pais) == ("APPLE.COM BILL", "CUPERTINO", "USA")
    assert (m.marca_tarjeta, m.ultimos4, m.autorizacion, m.referencia) == ("MASTERCARD", "4455", "112233", "528212345678")
    assert m.fecha == datetime(2025, 10, 9, 23, 15)


def test_bncr_voucher_con_negritas_en_una_linea():
    m = extraer("bncontacto@bncr.fi.cr", "Voucher Digital", BN_VOUCHER_2).movimiento
    assert (m.monto, m.moneda, m.comercio, m.ciudad) == (1249.99, "USD", "BEST BUY 0001234", "SCHAUMBURG")
    assert m.fecha == datetime(2025, 10, 12, 16, 20)


def test_bncr_sobre_es_interno():
    m = extraer("bncontacto@bncr.fi.cr", "BNCR – Aporte al Sobre N° 20012345678", BN_SOBRE).movimiento
    assert (m.tipo, m.direccion, m.monto, m.detalle["sobre"]) == ("aporte_sobre", "interno", 25000.0, "Caja chica")
    assert m.fecha == datetime(2025, 8, 15, 6, 0, 12)


def test_bncr_estado_de_cuenta_es_info():
    r = extraer("bncontacto@bncr.fi.cr", "Estado de Cuenta Tarjeta de Crédito", "Adjunto su estado de cuenta")
    assert (r.movimiento.tipo, r.movimiento.direccion) == ("estado_cuenta", "info")


def test_davibank_debito():
    m = extraer("alertas@davibank.cr", "Alertas DAVIbank Costa Rica", DAVI_DEBITO).movimiento
    assert (m.tipo, m.direccion, m.monto, m.moneda, m.ultimos4) == ("debito_cuenta", "salida", 15750.0, "CRC", "7788")
    assert m.fecha == datetime(2025, 9, 23, 14, 5, 33)


def test_davibank_cuotificacion_no_suma():
    m = extraer("alertas@davibank.cr", "Notificación Cuotificación de Compras", DAVI_CUOTAS).movimiento
    assert (m.tipo, m.direccion, m.monto, m.moneda, m.comercio) == ("cuotificacion", "info", 845.30, "USD", "COPA AIR 2301234567890")
    assert m.detalle["cuotas"] == 6


@pytest.mark.parametrize("remitente,asunto", [
    ("bncontacto@bncr.fi.cr", "BNCR Informa: Inicio de sesión en Canales Digitales"),
    ("bncontacto@bncr.fi.cr", "Comprobación Seguridad"),
    ("alertas@davibank.cr", "Su tarjeta ha sido desactivada en la billetera"),
    ("alertas@davibank.cr", "Transferencia fallida"),
    ("encuesta@surveymonkeyuser.com", "Cuéntenos su experiencia"),
])
def test_avisos_sin_dinero_se_ignoran(remitente, asunto):
    assert extraer(remitente, asunto, "texto").estado == "ignorado"


def test_correo_falso_de_otro_dominio_no_se_procesa():
    r = extraer("alertas@bac-credomatic-seguridad.com", "Notificación de transacción UBER", BAC_COMPRA_VIEJO)
    assert (r.estado, r.regla) == ("ignorado", "remitente_no_es_banco")


def test_dominio_viejo_de_scotiabank_cuenta_como_davibank():
    r = extraer("estadosdecuenta@scotiabank.com", "Estado de cuenta Tarjeta de Crédito", "Adjunto su estado de cuenta")
    assert (r.regla, r.movimiento.banco) == ("davibank_estado_cuenta", "Davibank")


def test_dominio_viejo_de_bac_cuenta_como_bac():
    r = extraer("notificaciones@bacsanjose.com", "Algo viejo", "texto que no conocemos")
    assert (r.estado, r.regla) == ("no_reconocido", "bac_formato_desconocido")


@pytest.mark.parametrize("remitente", ["alertas@scotiabank.com.seguridad-cr.com", "alertas@bacsanjose.co"])
def test_dominio_parecido_al_viejo_no_se_procesa(remitente):
    r = extraer(remitente, "Notificación de transacción UBER", BAC_COMPRA_VIEJO)
    assert (r.estado, r.regla) == ("ignorado", "remitente_no_es_banco")


def test_formato_nuevo_queda_como_no_reconocido():
    r = extraer("notificacion@notificacionesbaccr.com", "Algo nuevo", "texto que no conocemos")
    assert (r.estado, r.regla) == ("no_reconocido", "bac_formato_desconocido")


def test_comercio_con_palabra_ignorada_en_el_asunto_no_se_pierde():
    cuerpo = BAC_COMPRA_VIEJO.replace(" UBER\n", " ASOS LIMITED\n")
    r = extraer("notificacionbac@baccredomatic.cr", "Notificación de transacción ASOS LIMITED 14-09-2025 - 18:42", cuerpo)
    assert (r.estado, r.movimiento.comercio) == ("movimiento", "ASOS LIMITED")


def test_devolucion_de_compra_es_entrada():
    cuerpo = BAC_COMPRA_VIEJO.replace(" COMPRA\n", " DEVOLUCION DE COMPRA\n")
    m = extraer("notificacionbac@baccredomatic.cr", "Notificación de transacción UBER", cuerpo).movimiento
    assert (m.tipo, m.direccion) == ("devolucion", "entrada")
