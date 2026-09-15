"""Tests herméticos del cifrado de credenciales (RSA-OAEP-SHA256 + clave_encriptada).

No tocan la red ni las claves reales del servidor: se genera un par RSA efímero
y se reemplaza la descarga de la clave pública.
"""

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from mrbot_app import helpers, seguridad
from mrbot_app.seguridad import (
    CAMPO_CLAVE_ENCRIPTADA,
    CAMPOS_CLAVE_PLANA,
    ClaveEncriptacionError,
    base_url_de,
    cifrar_clave,
    invalidar_cache_clave_publica,
    obtener_clave_publica,
    preparar_payload,
    tiene_clave_plana,
)

BASE_URL = "https://api-bots.mrbot.com.ar"
URL_CONSULTA = BASE_URL + "/api/v1/rcel/consulta"
CLAVE = "clave-ñ-fiscal-123"


class _RespuestaFake:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("sin json")
        return self._payload


def _pem_publico(clave_privada):
    return clave_privada.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _descifrar(cifrado_b64, clave_privada):
    return clave_privada.decrypt(
        base64.b64decode(cifrado_b64),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    ).decode("utf-8")


@pytest.fixture(autouse=True)
def _limpiar_cache():
    invalidar_cache_clave_publica()
    yield
    invalidar_cache_clave_publica()


@pytest.fixture
def clave_rsa():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def endpoint_clave_publica(monkeypatch, clave_rsa):
    """Reemplaza el GET de la clave pública y cuenta cuántas veces se pidió."""
    llamados = {"n": 0}

    def fake_get(url, timeout=None):
        llamados["n"] += 1
        return _RespuestaFake(
            payload={
                "key_id": "abc",
                "algorithm": "RSA-OAEP-SHA256",
                "encoding": "base64",
                "public_key_pem": _pem_publico(clave_rsa),
            }
        )

    monkeypatch.setattr(seguridad.requests, "get", fake_get)
    return llamados


def test_base_url_de():
    assert base_url_de(URL_CONSULTA) == BASE_URL
    assert base_url_de("https://host:8443/x/y") == "https://host:8443"


def test_base_url_invalida_levanta_error():
    with pytest.raises(ClaveEncriptacionError):
        base_url_de("api/v1/consulta")


def test_round_trip_descifra_con_la_clave_privada(endpoint_clave_publica, clave_rsa):
    cifrado = cifrar_clave(CLAVE, BASE_URL)

    assert cifrado != CLAVE
    assert cifrado.isascii()
    assert _descifrar(cifrado, clave_rsa) == CLAVE


def test_clave_publica_se_cachea(endpoint_clave_publica):
    obtener_clave_publica(BASE_URL)
    obtener_clave_publica(BASE_URL)
    cifrar_clave(CLAVE, BASE_URL)

    assert endpoint_clave_publica["n"] == 1


def test_force_refresh_y_invalidacion_vuelven_a_pedir(endpoint_clave_publica):
    obtener_clave_publica(BASE_URL)
    obtener_clave_publica(BASE_URL, force_refresh=True)
    invalidar_cache_clave_publica(BASE_URL)
    obtener_clave_publica(BASE_URL)

    assert endpoint_clave_publica["n"] == 3


@pytest.mark.parametrize("campo", CAMPOS_CLAVE_PLANA)
def test_preparar_payload_cifra_y_borra_el_campo_plano(campo, endpoint_clave_publica, clave_rsa):
    payload = {"cuit_representante": "20123456789", campo: CLAVE}

    preparado = preparar_payload(payload, BASE_URL)

    assert campo not in preparado
    assert set(preparado) == {"cuit_representante", CAMPO_CLAVE_ENCRIPTADA}
    assert _descifrar(preparado[CAMPO_CLAVE_ENCRIPTADA], clave_rsa) == CLAVE
    assert payload[campo] == CLAVE


def test_preparar_payload_con_clave_vacia_no_manda_credencial(endpoint_clave_publica):
    preparado = preparar_payload({"cuit": "1", "clave": ""}, BASE_URL)

    assert preparado == {"cuit": "1"}


def test_preparar_payload_sin_credencial_no_pide_clave_publica(endpoint_clave_publica):
    preparado = preparar_payload({"cuit": "1"}, BASE_URL)

    assert preparado == {"cuit": "1"}
    assert endpoint_clave_publica["n"] == 0


def test_preparar_payload_con_clave_explicita(endpoint_clave_publica, clave_rsa):
    payload = {"cuit": "1", "clave": "otra-clave"}

    preparado = preparar_payload(payload, BASE_URL, clave=CLAVE)

    assert "clave" not in preparado
    assert _descifrar(preparado[CAMPO_CLAVE_ENCRIPTADA], clave_rsa) == CLAVE


def test_tiene_clave_plana():
    assert tiene_clave_plana({"clave": "x"})
    assert tiene_clave_plana({"contrasena": "x"})
    assert not tiene_clave_plana({"clave_encriptada": "x"})


def test_endpoint_caido_levanta_error(monkeypatch):
    def fake_get(url, timeout=None):
        raise seguridad.requests.RequestException("timeout")

    monkeypatch.setattr(seguridad.requests, "get", fake_get)

    with pytest.raises(ClaveEncriptacionError):
        cifrar_clave(CLAVE, BASE_URL)


@pytest.mark.parametrize(
    "payload",
    [
        {"algorithm": "RSA-OAEP-SHA1", "encoding": "base64", "public_key_pem": "x"},
        {"algorithm": "RSA-OAEP-SHA256", "encoding": "hex", "public_key_pem": "x"},
        {"algorithm": "RSA-OAEP-SHA256", "encoding": "base64", "public_key_pem": ""},
    ],
)
def test_contrato_inesperado_levanta_error(monkeypatch, payload):
    monkeypatch.setattr(seguridad.requests, "get", lambda url, timeout=None: _RespuestaFake(payload=payload))

    with pytest.raises(ClaveEncriptacionError):
        cifrar_clave(CLAVE, BASE_URL)


def test_endpoint_con_error_http_levanta_error(monkeypatch):
    monkeypatch.setattr(
        seguridad.requests,
        "get",
        lambda url, timeout=None: _RespuestaFake(status_code=503, payload={"detail": "sin clave"}),
    )

    with pytest.raises(ClaveEncriptacionError):
        cifrar_clave(CLAVE, BASE_URL)


def test_safe_post_no_manda_la_clave_en_texto_plano(monkeypatch, endpoint_clave_publica, clave_rsa):
    enviados = []

    def fake_post(url, headers=None, json=None, timeout=None):
        enviados.append(json)
        return _RespuestaFake(payload={"success": True})

    monkeypatch.setattr(helpers.requests, "post", fake_post)

    resultado = helpers.safe_post(URL_CONSULTA, {"x-api-key": "k"}, {"cuit_representante": "20", "clave": CLAVE})

    assert resultado["http_status"] == 200
    assert len(enviados) == 1
    payload_enviado = enviados[0]
    assert "clave" not in payload_enviado
    assert _descifrar(payload_enviado[CAMPO_CLAVE_ENCRIPTADA], clave_rsa) == CLAVE
    assert resultado["request_payload"] == payload_enviado


def test_safe_post_no_intenta_si_no_hay_credencial(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return _RespuestaFake(payload={"success": True})

    def fake_get(url, timeout=None):
        raise AssertionError("no deberia pedir la clave publica")

    monkeypatch.setattr(helpers.requests, "post", fake_post)
    monkeypatch.setattr(seguridad.requests, "get", fake_get)

    resultado = helpers.safe_post(URL_CONSULTA, {}, {"cuit": "1"})

    assert resultado["http_status"] == 200


def test_safe_post_falla_si_no_puede_cifrar(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        raise AssertionError("no deberia enviar el request")

    monkeypatch.setattr(helpers.requests, "post", fake_post)
    monkeypatch.setattr(
        seguridad.requests,
        "get",
        lambda url, timeout=None: _RespuestaFake(status_code=503, payload={}),
    )

    resultado = helpers.safe_post(URL_CONSULTA, {}, {"cuit": "20", "clave": CLAVE})

    assert resultado["http_status"] is None
    assert "No se pudo cifrar la clave fiscal" in resultado["data"]["message"]


def test_safe_post_invalida_la_cache_tras_error_de_descifrado(monkeypatch):
    llamados = {"get": 0}

    def fake_get(url, timeout=None):
        llamados["get"] += 1
        return _RespuestaFake(
            payload={
                "algorithm": "RSA-OAEP-SHA256",
                "encoding": "base64",
                "public_key_pem": _pem_publico(clave_vieja := rsa.generate_private_key(public_exponent=65537, key_size=2048)),
            }
        )

    def fake_post(url, headers=None, json=None, timeout=None):
        return _RespuestaFake(status_code=422, payload={"detail": "No se pudo desencriptar la credencial"})

    monkeypatch.setattr(seguridad.requests, "get", fake_get)
    monkeypatch.setattr(helpers.requests, "post", fake_post)

    resultado = helpers.safe_post(URL_CONSULTA, {}, {"cuit": "20", "clave": CLAVE})

    assert resultado["http_status"] == 422
    assert llamados["get"] == 1

    # La clave publica quedo invalidada: la proxima consulta la vuelve a pedir.
    helpers.safe_post(URL_CONSULTA, {}, {"cuit": "20", "clave": CLAVE})
    assert llamados["get"] == 2


def test_safe_post_conserva_la_cache_con_otros_422(monkeypatch, endpoint_clave_publica):
    def fake_post(url, headers=None, json=None, timeout=None):
        return _RespuestaFake(status_code=422, payload={"detail": "La clave es obligatoria"})

    monkeypatch.setattr(helpers.requests, "post", fake_post)

    resultado = helpers.safe_post(URL_CONSULTA, {}, {"cuit": "20", "clave": CLAVE})

    assert resultado["http_status"] == 422
    assert endpoint_clave_publica["n"] == 1

    helpers.safe_post(URL_CONSULTA, {}, {"cuit": "20", "clave": CLAVE})
    assert endpoint_clave_publica["n"] == 1
