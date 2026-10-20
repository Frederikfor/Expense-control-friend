"""
Pruebas del filtro que decide que PDF son de bancos (muestras_pdf.py).
Correr desde la carpeta scripts:
    python -m pytest tests
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from muestras_pdf import categoria_pdf, patron_de_nombre  # noqa: E402


def test_pdf_de_banco_verificado_se_usa():
    assert categoria_pdf("davibank.cr", True, True) == "banco"


def test_pdf_de_otro_remitente_no_se_usa_aunque_diga_estado_de_cuenta():
    assert categoria_pdf("ccss.sa.cr", True, True) == "otro_remitente"


def test_pdf_de_banco_sin_dkim_ni_spf_no_se_usa():
    assert categoria_pdf("bncr.fi.cr", False, True) == "banco_sin_verificar"


def test_pdf_de_banco_sin_cabecera_de_autenticacion_no_se_usa():
    assert categoria_pdf("bncr.fi.cr", None, True) == "banco_sin_verificar"


def test_pdf_sin_correo_en_la_base():
    assert categoria_pdf("", None, False) == "sin_correo"


def test_dominio_parecido_al_del_banco_no_pasa():
    assert categoria_pdf("davibank.cr.estafa.com", True, True) == "otro_remitente"


def test_patron_quita_el_id_del_correo_y_tapa_digitos():
    assert patron_de_nombre("0117d82e49__Extracto_202005_0000_2210.pdf") == "_extracto_999999_9999_9999.pdf"
    assert patron_de_nombre("02dd838698_Tarjeta_7966.PDF") == "tarjeta_9999.pdf"
