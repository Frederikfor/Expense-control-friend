"""
Carga y valida config/bancos.toml: los datos de bancos, dominios y palabras
que usan los extractores. Si el archivo tiene un error, se detiene con un
mensaje que dice que corregir, en vez de fallar mas adelante sin explicacion.
"""

import tomllib
from pathlib import Path

RUTA_CONFIG = Path(__file__).resolve().parents[1] / "config" / "bancos.toml"

DIRECCIONES = {"salida", "entrada", "interno", "info"}


class ErrorConfig(ValueError):
    """El archivo de configuracion tiene un dato faltante o invalido."""


def _exigir(condicion, mensaje, ruta):
    if not condicion:
        raise ErrorConfig(f"{ruta.name}: {mensaje}")


def validar(datos, ruta=RUTA_CONFIG):
    """Revisa que esten todas las secciones y que no haya datos repetidos o con forma rara."""
    for seccion in ("bancos", "correos", "idioma", "tipos_por_palabra", "tipo_desconocido"):
        _exigir(seccion in datos, f"falta la seccion [{seccion}]", ruta)

    _exigir(datos["bancos"], "no hay ningun banco en [bancos]", ruta)
    duenos = {}
    for banco, info in datos["bancos"].items():
        dominios = info.get("dominios")
        _exigir(isinstance(dominios, list) and dominios, f"el banco {banco} no tiene 'dominios'", ruta)
        for dominio in dominios:
            d = dominio.strip().lower()
            _exigir(d and "@" not in d, f"dominio invalido en {banco}: '{dominio}' (va sin @)", ruta)
            _exigir(d not in duenos, f"el dominio '{d}' esta en {duenos.get(d)} y en {banco}", ruta)
            duenos[d] = banco

    _exigir(isinstance(datos["correos"].get("asuntos_ignorados"), list),
            "falta 'asuntos_ignorados' en [correos]", ruta)
    for clave in ("meses", "monedas", "paises_vacios"):
        _exigir(clave in datos["idioma"], f"falta '{clave}' en [idioma]", ruta)
    for mes, numero in datos["idioma"]["meses"].items():
        _exigir(len(mes) == 3 and 1 <= numero <= 12, f"mes invalido: {mes} = {numero}", ruta)

    for regla in [*datos["tipos_por_palabra"], datos["tipo_desconocido"]]:
        for clave in ("tipo", "direccion", "confianza"):
            _exigir(clave in regla, f"falta '{clave}' en una regla de tipos: {regla}", ruta)
        _exigir(regla["direccion"] in DIRECCIONES,
                f"direccion '{regla['direccion']}' invalida; usar una de {sorted(DIRECCIONES)}", ruta)
        _exigir(0 <= regla["confianza"] <= 1, f"confianza fuera de 0..1 en {regla}", ruta)
    for regla in datos["tipos_por_palabra"]:
        _exigir(regla.get("palabras"), f"regla de tipo '{regla['tipo']}' sin 'palabras'", ruta)
    return datos


def cargar_config(ruta=RUTA_CONFIG):
    ruta = Path(ruta)
    if not ruta.exists():
        raise ErrorConfig(f"no se encontro el archivo de configuracion: {ruta}")
    try:
        datos = tomllib.loads(ruta.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ErrorConfig(f"{ruta.name} tiene un error de formato: {error}") from error
    return validar(datos, ruta)


def dominios_por_banco(config):
    """{'baccredomatic.cr': 'BAC', ...} a partir de [bancos]."""
    return {d.strip().lower(): banco for banco, info in config["bancos"].items() for d in info["dominios"]}
