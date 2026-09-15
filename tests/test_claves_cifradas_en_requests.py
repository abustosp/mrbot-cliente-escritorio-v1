"""Regresión: ningún request de bots puede mandar la clave fiscal en texto plano.

Recorre los caminos reales de envío (safe_post y los POST inline de cada módulo)
con la red mockeada y verifica que el payload que sale lleva `clave_encriptada`
y ninguno de los campos en claro, y que el ciphertext descifra con la clave
privada del par efímero.
"""

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from mrbot_app import carga_iva_simple, facturometro, helpers, mis_comprobantes, portal_iva, seguridad, srt_alicuotas
from mrbot_app.ret_per_provinciales import consulta_agip, consulta_arba, consulta_misiones
from mrbot_app.seguridad import CAMPO_CLAVE_ENCRIPTADA, CAMPOS_CLAVE_PLANA

BASE_URL = "https://api-bots.mrbot.com.ar"
CONFIG = (BASE_URL, "api-key-test", "test@ejemplo.com")
CLAVE = "clave-ñ-fiscal-123"
CUIT = "20123456789"

CLAVE_PRIVADA = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _RespuestaFake:
    status_code = 200
    text = "{}"

    def json(self):
        return {"success": True}


@pytest.fixture(autouse=True)
def _mock_red(monkeypatch):
    enviados = []

    def fake_post(url, headers=None, json=None, data=None, files=None, params=None, timeout=None):
        enviados.append({"url": url, "json": json, "data": data})
        return _RespuestaFake()

    monkeypatch.setattr(helpers.requests, "post", fake_post)
    monkeypatch.setattr(seguridad, "obtener_clave_publica", lambda base_url, force_refresh=False: CLAVE_PRIVADA.public_key())
    seguridad.invalidar_cache_clave_publica()
    return enviados


def _payload_enviado(enviado):
    return enviado["json"] if enviado["json"] is not None else enviado["data"]


def _descifrar(cifrado_b64):
    return CLAVE_PRIVADA.decrypt(
        base64.b64decode(cifrado_b64),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    ).decode("utf-8")


def _assert_cifrado(enviados, endpoint):
    assert len(enviados) == 1, f"se esperaba un solo request a {endpoint}"
    assert enviados[0]["url"].endswith(endpoint)
    payload = _payload_enviado(enviados[0])
    for campo in CAMPOS_CLAVE_PLANA:
        assert campo not in payload, f"{campo} viajo en texto plano a {endpoint}"
    assert CAMPO_CLAVE_ENCRIPTADA in payload, f"falta clave_encriptada en {endpoint}"
    assert _descifrar(payload[CAMPO_CLAVE_ENCRIPTADA]) == CLAVE


def test_mis_comprobantes(_mock_red):
    mis_comprobantes.consulta_mc(
        desde="01/01/2024",
        hasta="31/01/2024",
        cuit_inicio_sesion=CUIT,
        representado_nombre="TEST",
        representado_cuit=CUIT,
        contrasena=CLAVE,
        descarga_emitidos=True,
        descarga_recibidos=True,
        log_fn=lambda m: None,
    )

    _assert_cifrado(_mock_red, "/api/v1/mis_comprobantes/consulta")


def test_portal_iva(_mock_red):
    portal_iva.consulta_portal_iva(
        cuit_representante=CUIT,
        clave_representante=CLAVE,
        cuit_representado=CUIT,
        denominacion="TEST",
        periodo="202401",
        log_fn=lambda m: None,
    )

    _assert_cifrado(_mock_red, "/api/v1/portal_iva/consulta")


def test_carga_iva_simple_multipart(_mock_red):
    carga_iva_simple.carga_iva_simple(
        cuit_representante=CUIT,
        clave_representante=CLAVE,
        cuit_representado=CUIT,
        periodo="202401",
        archivos={},
        log_fn=lambda m: None,
    )

    _assert_cifrado(_mock_red, "/api/v1/portal_iva/carga")


def test_ret_per_arba(_mock_red):
    consulta_arba(cuit=CUIT, clave=CLAVE, periodo="202401", denominacion="TEST", log_fn=lambda m: None)

    _assert_cifrado(_mock_red, "/api/v1/retenciones_percepciones_iibb/arba/consulta")


def test_ret_per_agip(_mock_red):
    consulta_agip(
        usuario="usuario-test",
        clave=CLAVE,
        cuit_representado=CUIT,
        denominacion="TEST",
        desde="202401",
        hasta="202401",
        log_fn=lambda m: None,
    )

    _assert_cifrado(_mock_red, "/api/v1/retenciones_percepciones_iibb/agip/consulta")


def test_ret_per_misiones(_mock_red):
    consulta_misiones(
        cuit_representante=CUIT,
        clave_representante=CLAVE,
        cuit_representado=CUIT,
        denominacion="TEST",
        desde="202401",
        hasta="202401",
        log_fn=lambda m: None,
    )

    _assert_cifrado(_mock_red, "/api/v1/retenciones_percepciones_iibb/misiones/consulta")


def test_facturometro(_mock_red):
    facturometro.consultar_facturometro(
        cuit_login=CUIT,
        clave=CLAVE,
        config=CONFIG,
        cuit_representado=CUIT,
        log_fn=lambda m: None,
    )

    _assert_cifrado(_mock_red, "/api/v1/facturometro/consulta")


def test_srt_alicuotas_y_payload_expuesto(_mock_red):
    respuesta = srt_alicuotas.consultar_srt_alicuotas(
        base_url=BASE_URL,
        api_key="api-key-test",
        email="test@ejemplo.com",
        cuit_login=CUIT,
        clave=CLAVE,
        cuits_consulta=[CUIT],
    )

    _assert_cifrado(_mock_red, "/api/v1/srt/alicuotas/consulta")

    # El payload que queda adjunto a la respuesta (y se loguea) es el enviado, sin la clave en claro.
    adjunto = respuesta["request_payload"]
    assert CAMPO_CLAVE_ENCRIPTADA in adjunto
    for campo in CAMPOS_CLAVE_PLANA:
        assert campo not in adjunto
    assert _descifrar(adjunto[CAMPO_CLAVE_ENCRIPTADA]) == CLAVE
    assert "request_payload_safe" not in respuesta


def test_safe_post_no_manda_texto_plano_sin_importar_el_campo(_mock_red):
    for campo in CAMPOS_CLAVE_PLANA:
        _mock_red.clear()
        helpers.safe_post(BASE_URL + "/api/v1/rcel/consulta", {}, {"cuit": CUIT, campo: CLAVE})
        _assert_cifrado(_mock_red, "/api/v1/rcel/consulta")


def test_payload_de_la_api_de_usuarios_no_se_toca(_mock_red):
    # Endpoints sin credencial fiscal: el payload se envía tal cual y sin request_payload extra.
    resultado = helpers.safe_post(BASE_URL + "/api/v1/user/", {}, {"mail": "test@ejemplo.com"})

    assert _payload_enviado(_mock_red[0]) == {"mail": "test@ejemplo.com"}
    assert "request_payload" not in resultado


CASOS_LOG = [
    (
        "mis_comprobantes",
        lambda log: mis_comprobantes.consulta_mc(
            desde="01/01/2024",
            hasta="31/01/2024",
            cuit_inicio_sesion=CUIT,
            representado_nombre="TEST",
            representado_cuit=CUIT,
            contrasena=CLAVE,
            descarga_emitidos=True,
            descarga_recibidos=True,
            log_fn=log,
        ),
    ),
    (
        "portal_iva",
        lambda log: portal_iva.consulta_portal_iva(
            cuit_representante=CUIT,
            clave_representante=CLAVE,
            cuit_representado=CUIT,
            denominacion="TEST",
            periodo="202401",
            log_fn=log,
        ),
    ),
    (
        "ret_per_arba",
        lambda log: consulta_arba(cuit=CUIT, clave=CLAVE, periodo="202401", denominacion="TEST", log_fn=log),
    ),
    (
        "carga_iva_simple",
        lambda log: carga_iva_simple.carga_iva_simple(
            cuit_representante=CUIT,
            clave_representante=CLAVE,
            cuit_representado=CUIT,
            periodo="202401",
            archivos={},
            log_fn=log,
        ),
    ),
    (
        "facturometro",
        lambda log: facturometro.consultar_facturometro(
            cuit_login=CUIT,
            clave=CLAVE,
            config=CONFIG,
            cuit_representado=CUIT,
            log_fn=log,
        ),
    ),
]


@pytest.mark.parametrize("nombre,ejecutar", CASOS_LOG, ids=[c[0] for c in CASOS_LOG])
def test_log_del_modulo_muestra_el_body_enviado(_mock_red, nombre, ejecutar):
    log: list = []
    ejecutar(log.append)

    payload = _payload_enviado(_mock_red[-1])
    texto = "\n".join(str(linea) for linea in log)

    assert payload["clave_encriptada"] in texto, f"{nombre} no loguea el body enviado"
    assert "***" not in texto, f"{nombre} censura clave_encriptada en el log"
    assert CLAVE not in texto, f"{nombre} escribe la clave en texto plano en el log"
