"""
Pruebas de config/bancos.toml y de su validacion (configuracion.py).
Correr desde la carpeta scripts:
    python -m pytest tests
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configuracion import ErrorConfig, cargar_config, dominios_por_banco  # noqa: E402
from extractores import extraer, tipo_por_palabra  # noqa: E402

MINIMA = """
[bancos.BancoX]
dominios = ["bancox.cr"]

[correos]
asuntos_ignorados = []

[idioma]
meses = { ene = 1 }
monedas = {}
paises_vacios = []

[[tipos_por_palabra]]
palabras = ["compra"]
tipo = "compra"
direccion = "salida"
confianza = 1.0

[tipo_desconocido]
tipo = "otro"
direccion = "salida"
confianza = 0.5
"""


def escribir(tmp_path, texto):
    ruta = tmp_path / "bancos.toml"
    ruta.write_text(texto, encoding="utf-8")
    return ruta


def test_la_configuracion_real_es_valida():
    config = cargar_config()
    assert {"BAC", "BNCR", "Davibank"} <= set(config["bancos"])


def test_configuracion_minima_es_valida(tmp_path):
    config = cargar_config(escribir(tmp_path, MINIMA))
    assert dominios_por_banco(config) == {"bancox.cr": "BancoX"}


def test_dominio_en_dos_bancos_se_rechaza(tmp_path):
    texto = MINIMA + '\n[bancos.BancoY]\ndominios = ["BancoX.cr"]\n'
    with pytest.raises(ErrorConfig, match="bancox.cr"):
        cargar_config(escribir(tmp_path, texto))


def test_dominio_con_arroba_se_rechaza(tmp_path):
    with pytest.raises(ErrorConfig, match="sin @"):
        cargar_config(escribir(tmp_path, MINIMA.replace('"bancox.cr"', '"alertas@bancox.cr"')))


def test_seccion_faltante_se_rechaza(tmp_path):
    with pytest.raises(ErrorConfig, match=r"\[correos\]"):
        cargar_config(escribir(tmp_path, MINIMA.replace("[correos]\nasuntos_ignorados = []", "")))


def test_direccion_invalida_se_rechaza(tmp_path):
    with pytest.raises(ErrorConfig, match="direccion 'gasto'"):
        cargar_config(escribir(tmp_path, MINIMA.replace('direccion = "salida"', 'direccion = "gasto"', 1)))


def test_error_de_formato_toml_da_mensaje_claro(tmp_path):
    with pytest.raises(ErrorConfig, match="error de formato"):
        cargar_config(escribir(tmp_path, MINIMA + "\n[bancos.BancoZ\n"))


def test_tipos_por_palabra_salen_de_la_configuracion(tmp_path):
    config = cargar_config(escribir(tmp_path, MINIMA))
    assert tipo_por_palabra("COMPRA", config) == ("compra", "salida", 1.0)
    assert tipo_por_palabra("DEVOLUCION", config) == ("otro", "salida", 0.5)


def test_banco_en_configuracion_sin_lector_queda_no_reconocido(monkeypatch):
    import extractores
    monkeypatch.setitem(extractores.DOMINIOS_BANCO, "bancox.cr", "BancoX")
    r = extraer("alertas@bancox.cr", "Compra", "texto")
    assert (r.estado, r.regla) == ("no_reconocido", "bancox_formato_desconocido")
