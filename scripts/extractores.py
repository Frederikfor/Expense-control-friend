"""
Lectores (extractores) de correos de BAC, Banco Nacional y Davibank.

Solo funciones puras: reciben remitente, asunto, cuerpo y fecha del correo,
y devuelven un Movimiento (o dicen por que el correo se ignora). No leen
archivos ni bases de datos, asi se pueden probar facil con pytest.

Formatos soportados (vistos en las muestras de octubre 2026):
  BAC       - Notificacion de transaccion (compras con tarjeta, 2 remitentes)
            - Notificacion de Transferencia SINPE
  BNCR      - Voucher Digital (compras con tarjeta)
            - Aporte / Retiro del Sobre (movimientos internos)
            - Estados de cuenta (solo se marcan; el PDF se lee aparte)
  Davibank  - Alertas: debito o credito a la cuenta
            - Cuotificacion de compras (informativo, no es gasto nuevo)
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

from configuracion import cargar_config, dominios_por_banco

# Los datos (bancos, dominios, palabras, meses, monedas) viven en config/bancos.toml.
# Aqui solo quedan las regex de cada formato de correo.
CONFIG = cargar_config()
DOMINIOS_BANCO = dominios_por_banco(CONFIG)
ASUNTOS_IGNORADOS = [a.lower() for a in CONFIG["correos"]["asuntos_ignorados"]]
MESES = CONFIG["idioma"]["meses"]
MONEDAS_TEXTO = CONFIG["idioma"]["monedas"]
PAISES_VACIOS = {p.lower() for p in CONFIG["idioma"]["paises_vacios"]}


@dataclass
class Movimiento:
    banco: str
    tipo: str            # compra, sinpe_enviado, sinpe_recibido, debito_cuenta, credito_cuenta,
                         # aporte_sobre, retiro_sobre, retiro_efectivo, devolucion, cuotificacion, estado_cuenta
    direccion: str       # salida (sale dinero), entrada, interno (entre tus cuentas), info (no se suma)
    fecha: datetime | None = None
    monto: float | None = None
    moneda: str | None = None
    comercio: str | None = None
    ciudad: str | None = None
    pais: str | None = None
    marca_tarjeta: str | None = None
    ultimos4: str | None = None
    autorizacion: str | None = None
    referencia: str | None = None
    confianza: float = 1.0
    detalle: dict = field(default_factory=dict)

    def a_dict(self):
        return asdict(self)


@dataclass
class Resultado:
    estado: str          # movimiento, ignorado, no_reconocido
    regla: str
    movimiento: Movimiento | None = None


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------

def aplanar(texto):
    """Quita asteriscos de negrita (*COMPRA*) y deja todo en una linea con espacios simples.
    Los asteriscos seguidos (************1234) se conservan: son el numero de tarjeta tapado."""
    sin_negrita = re.sub(r"(?<!\*)\*(?!\*)", " ", texto or "")
    return re.sub(r"\s+", " ", sin_negrita).strip()


def parse_monto(texto):
    """Convierte '12,500.00', '12.500,00', '9999,99', '.50' o '1500' en float.

    Regla: si el ultimo separador (. o ,) va seguido de exactamente 2 digitos al
    final, es el decimal; los demas separadores son de miles.
    """
    t = texto.strip().rstrip(".,")
    m = re.search(r"[.,](\d{2})$", t)
    if m:
        entero = re.sub(r"[.,]", "", t[: m.start()]) or "0"
        return float(f"{entero}.{m.group(1)}")
    return float(re.sub(r"[.,]", "", t))


def moneda_de(texto):
    t = texto.strip().lower()
    return MONEDAS_TEXTO.get(t, texto.strip().upper())


def _hora_24(hora, minuto, ampm):
    hora = int(hora)
    if ampm:
        ampm = ampm.lower()[0]
        if ampm == "p" and hora < 12:
            hora += 12
        elif ampm == "a" and hora == 12:
            hora = 0
    return hora, int(minuto)


def fecha_numerica(a, b, anio, hora=0, minuto=0, segundo=0, referencia=None):
    """Arma una fecha de 'a/b/anio' sin saber si es dia/mes o mes/dia.

    Costa Rica usa dia/mes, pero si la fecha del correo (referencia) queda mas
    cerca con mes/dia, se usa esa. Si solo una opcion es valida, se usa esa.
    """
    anio = int(anio)
    anio += 2000 if anio < 100 else 0
    opciones = []
    for dia, mes in ((int(a), int(b)), (int(b), int(a))):
        try:
            opciones.append(datetime(anio, mes, dia, int(hora), int(minuto), int(segundo)))
        except ValueError:
            pass
    if not opciones:
        return None
    if referencia is None or len(opciones) == 1:
        return opciones[0]
    return min(opciones, key=lambda f: abs((f - referencia).total_seconds()))


def fecha_con_mes(mes_txt, dia, anio, hora=0, minuto=0, ampm=None):
    mes = MESES.get(mes_txt[:3].lower())
    if not mes:
        return None
    h, m = _hora_24(hora, minuto, ampm)
    try:
        return datetime(int(anio), mes, int(dia), h, m)
    except ValueError:
        return None


def separar_lugar(lugar):
    """'SAN JOSE, Costa Rica' -> ('SAN JOSE', 'Costa Rica'); ', Estados Unidos' -> (None, 'Estados Unidos')."""
    if "," not in lugar:
        return (lugar.strip() or None), None
    ciudad, pais = lugar.rsplit(",", 1)
    ciudad, pais = ciudad.strip(), pais.strip()
    if pais.lower() in PAISES_VACIOS:
        pais = None
    return (ciudad or None), (pais or None)


def tipo_por_palabra(palabra, config=CONFIG):
    """Traduce 'COMPRA', 'DEVOLUCION', 'RETIRO'... a (tipo, direccion, confianza)
    con las reglas [[tipos_por_palabra]] de la configuracion, en orden."""
    p = palabra.lower()
    for regla in config["tipos_por_palabra"]:
        if any(x.lower() in p for x in regla["palabras"]):
            return regla["tipo"], regla["direccion"], regla["confianza"]
    r = config["tipo_desconocido"]
    return r["tipo"], r["direccion"], r["confianza"]


# ----------------------------------------------------------------------------
# BAC
# ----------------------------------------------------------------------------

RE_BAC_COMPRA = re.compile(
    r"Comercio:\s*(?P<comercio>.+?)\s*Ciudad y país:\s*(?P<lugar>.*?)\s*Fecha:\s*"
    r"(?P<mes>[A-Za-zé]{3,})\.?\s+(?P<dia>\d{1,2}),\s*(?P<anio>\d{4})\s*,\s*(?P<hora>\d{1,2}):(?P<min>\d{2})\s*"
    r"(?P<marca>[A-Z][A-Z ]*?):?\s*\*+(?P<ult4>\d{4})\s*"
    r"Autorización:\s*(?P<aut>\w*)\s*Referencia:\s*(?P<ref>\d*)\s*"
    r"Tipo de Transacción:\s*(?P<tipo>.+?)\s*Monto:\s*(?P<moneda>[A-Z]{3})\s*(?P<monto>[\d.,]*\d)"
)

RE_BAC_SINPE = re.compile(
    r"transferencia SINPE.*?(?P<dir>debitando|acreditando)\s+(?:a\s+)?su cuenta\s+(?:IBAN\s+)?(?P<cuenta>\S+)\s+"
    r"por un monto de\s+(?P<monto>[\d.,]*\d)\s+(?P<moneda>Colones|Dólares|Dolares)\.?\s*"
    r"Día y hora:\s*(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<anio>\d{4})\s+(?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2})\s*"
    r"(?P<ampm>[ap])\.?\s*m\.?",
    re.IGNORECASE,
)


def bac(asunto, texto, fecha_correo, cuerpo_original):
    m = RE_BAC_COMPRA.search(texto)
    if m:
        tipo, direccion, confianza = tipo_por_palabra(m["tipo"])
        ciudad, pais = separar_lugar(m["lugar"])
        return Resultado("movimiento", "bac_compra", Movimiento(
            banco="BAC", tipo=tipo, direccion=direccion,
            fecha=fecha_con_mes(m["mes"], m["dia"], m["anio"], m["hora"], m["min"]),
            monto=parse_monto(m["monto"]), moneda=m["moneda"], comercio=m["comercio"].strip(),
            ciudad=ciudad, pais=pais, marca_tarjeta=m["marca"].strip(), ultimos4=m["ult4"],
            autorizacion=m["aut"] or None, referencia=m["ref"] or None, confianza=confianza,
            detalle={"tipo_banco": m["tipo"].strip()},
        ))
    m = RE_BAC_SINPE.search(texto)
    if m:
        h, mi = _hora_24(m["h"], m["mi"], m["ampm"])
        salida = m["dir"].lower().startswith("debit")
        return Resultado("movimiento", "bac_sinpe", Movimiento(
            banco="BAC", tipo="sinpe_enviado" if salida else "sinpe_recibido",
            direccion="salida" if salida else "entrada",
            fecha=fecha_numerica(m["a"], m["b"], m["anio"], h, mi, m["s"], fecha_correo),
            monto=parse_monto(m["monto"]), moneda=moneda_de(m["moneda"]),
            ultimos4=re.sub(r"\D", "", m["cuenta"])[-4:] or None,
        ))
    return None


# ----------------------------------------------------------------------------
# Banco Nacional
# ----------------------------------------------------------------------------

RE_BN_VOUCHER_TIPO = re.compile(r"comprobante de\s*(?P<tipo>.+?)\s*realizada en\s+(?P<comercio>.+?)\s+el\s+\d", re.S)
RE_BN_VOUCHER_FECHA = re.compile(
    r"(?P<mes>[A-Za-z]{3})\w*\.?\s+(?P<dia>\d{1,2}),\s*(?P<anio>\d{4})\s*-\s*(?P<hora>\d{1,2}):(?P<min>\d{2})"
    r"(?:\s*(?P<ampm>[ap])\.?\s*m\.?)?"
)
RE_BN_VOUCHER_TARJETA = re.compile(r"(?P<marca>MASTERCARD|VISA|AMEX|[A-Z]{3,})\s*\*+(?P<ult4>\d{4})")
RE_BN_TOTAL = re.compile(r"TOTAL:\s*(?P<moneda>[A-Z]{3})\s*(?P<monto>[\d.,]*\d)")
RE_BN_AUT = re.compile(r"NRO\.\s*AUT:\s*(\w+)")
RE_BN_REF = re.compile(r"REF:\s*(\w+)")

RE_BN_SOBRE = re.compile(
    r"(?P<accion>aporte al|retiro de dinero del) Sobre N° (?P<sobre>\d+)(?:\s*-\s*(?P<nombre>.+?))?\s+a nombre de .*?"
    r"realizado el día (?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<anio>\d{4}) a las (?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2}).*?"
    r"por un monto de (?P<monto>[\d.,]*\d) (?P<moneda>colones|dólares|dolares)",
    re.IGNORECASE,
)


def bncr(asunto, texto, fecha_correo, cuerpo_original):
    a = asunto.lower()
    if "estado de cuenta" in a:
        return Resultado("movimiento", "bncr_estado_cuenta",
                         Movimiento(banco="BNCR", tipo="estado_cuenta", direccion="info", fecha=fecha_correo))
    if "voucher" in a:
        crudo = re.sub(r"(?<!\*)\*(?!\*)", "", cuerpo_original or "")
        mt, mf, mc, mto = (RE_BN_VOUCHER_TIPO.search(crudo), RE_BN_VOUCHER_FECHA.search(texto),
                           RE_BN_VOUCHER_TARJETA.search(texto), RE_BN_TOTAL.search(texto))
        if not (mt and mto):
            return None
        # El comercio viene en columnas fijas: "NOMBRE      CIUDAD     PAIS"
        partes = [p.strip() for p in re.split(r"\s{2,}", mt["comercio"].strip()) if p.strip()]
        comercio = partes[0] if partes else mt["comercio"].strip()
        ciudad = partes[1] if len(partes) > 2 else None
        pais = partes[-1] if len(partes) > 1 else None
        tipo, direccion, confianza = tipo_por_palabra(mt["tipo"])
        fecha = fecha_con_mes(mf["mes"], mf["dia"], mf["anio"], mf["hora"], mf["min"], mf["ampm"]) if mf else None
        aut, ref = RE_BN_AUT.search(texto), RE_BN_REF.search(texto)
        return Resultado("movimiento", "bncr_voucher", Movimiento(
            banco="BNCR", tipo=tipo, direccion=direccion, fecha=fecha or fecha_correo,
            monto=parse_monto(mto["monto"]), moneda=mto["moneda"], comercio=comercio, ciudad=ciudad, pais=pais,
            marca_tarjeta=mc["marca"] if mc else None, ultimos4=mc["ult4"] if mc else None,
            autorizacion=aut[1] if aut else None, referencia=ref[1] if ref else None,
            confianza=confianza if fecha else confianza * 0.8, detalle={"tipo_banco": mt["tipo"].strip()},
        ))
    m = RE_BN_SOBRE.search(texto)
    if m:
        aporte = m["accion"].lower().startswith("aporte")
        return Resultado("movimiento", "bncr_sobre", Movimiento(
            banco="BNCR", tipo="aporte_sobre" if aporte else "retiro_sobre", direccion="interno",
            fecha=fecha_numerica(m["a"], m["b"], m["anio"], m["h"], m["mi"], m["s"], fecha_correo),
            monto=parse_monto(m["monto"]), moneda=moneda_de(m["moneda"]),
            detalle={"sobre": (m["nombre"] or m["sobre"]).strip()},
        ))
    return None


# ----------------------------------------------------------------------------
# Davibank
# ----------------------------------------------------------------------------

RE_DAVI_ALERTA = re.compile(
    r"Se realizó un (?P<dir>débito|debito|crédito|credito) a la cuenta bancaria en (?P<moneda_cta>[A-Z]{3}) "
    r"terminada en \**(?P<ult4>\d{4})\s*,\s*por un monto de (?P<moneda>[A-Z]{3}) (?P<monto>[\d.,]*\d)\s*\.?\s*"
    r"La transacción se realizó el día (?P<a>\d{1,2})-(?P<b>\d{1,2})-(?P<anio>\d{4})\s+"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2})",
    re.IGNORECASE,
)
RE_DAVI_CUOTAS = re.compile(
    r"compra realizada el (?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<anio>\d{4}) (?P<h>\d{1,2}):(?P<mi>\d{2}):(?P<s>\d{2}).*?"
    r"Nombre del comercio:\s*(?P<comercio>.+?)\s*Monto de la compra:\s*(?P<simbolo>[$¢₡]|[A-Z]{3})\s*(?P<monto>[\d.,]*\d)"
    r"\s*Cantidad de cuotas:\s*(?P<cuotas>\d+)",
    re.IGNORECASE,
)


def davibank(asunto, texto, fecha_correo, cuerpo_original):
    m = RE_DAVI_ALERTA.search(texto)
    if m:
        salida = m["dir"].lower().startswith("d")
        return Resultado("movimiento", "davibank_alerta", Movimiento(
            banco="Davibank", tipo="debito_cuenta" if salida else "credito_cuenta",
            direccion="salida" if salida else "entrada",
            fecha=fecha_numerica(m["a"], m["b"], m["anio"], m["h"], m["mi"], m["s"], fecha_correo),
            monto=parse_monto(m["monto"]), moneda=m["moneda"], ultimos4=m["ult4"],
            confianza=0.9,  # no trae comercio: hay que categorizarlo a mano o con el estado de cuenta
        ))
    m = RE_DAVI_CUOTAS.search(texto)
    if m:
        return Resultado("movimiento", "davibank_cuotas", Movimiento(
            banco="Davibank", tipo="cuotificacion", direccion="info",
            fecha=fecha_numerica(m["a"], m["b"], m["anio"], m["h"], m["mi"], m["s"], fecha_correo),
            monto=parse_monto(m["monto"]), moneda=moneda_de(m["simbolo"]), comercio=m["comercio"].strip(),
            detalle={"cuotas": int(m["cuotas"])},
        ))
    if "estado de cuenta" in asunto.lower():
        return Resultado("movimiento", "davibank_estado_cuenta",
                         Movimiento(banco="Davibank", tipo="estado_cuenta", direccion="info", fecha=fecha_correo))
    return None


# ----------------------------------------------------------------------------
# Punto de entrada
# ----------------------------------------------------------------------------

# Lector de correos de cada banco de la configuracion. Un banco que este en
# bancos.toml pero no aqui (ej. uno recien agregado) queda como no_reconocido.
LECTORES = {"BAC": bac, "BNCR": bncr, "Davibank": davibank}


def extraer(remitente_correo, asunto, cuerpo, fecha_correo=None):
    """Clasifica un correo y, si trae dinero, devuelve el Movimiento."""
    dominio = (remitente_correo or "").lower().rsplit("@", 1)[-1]
    banco = DOMINIOS_BANCO.get(dominio)
    asunto = asunto or ""
    if not banco:
        return Resultado("ignorado", "remitente_no_es_banco")
    lector = LECTORES.get(banco)
    r = lector(asunto, aplanar(cuerpo), fecha_correo, cuerpo) if lector else None
    if r:
        return r
    # La lista de ignorados se revisa DESPUES de intentar leer el correo: en BAC el
    # asunto trae el comercio, y un comercio como "ASOS LIMITED" no debe ignorarse.
    if any(x in asunto.lower() for x in ASUNTOS_IGNORADOS):
        return Resultado("ignorado", "aviso_sin_dinero")
    return Resultado("no_reconocido", f"{banco.lower()}_formato_desconocido")
