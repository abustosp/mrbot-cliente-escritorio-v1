"""Tests herméticos del retry con backoff y la secuencialidad del módulo BCRA.

No tocan la red: se reemplazan requests.get y time.sleep mediante monkeypatch.
"""

import threading
import time as time_module

from requests.exceptions import SSLError

from mrbot_app import bcra


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="{}"):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"status": status_code}
        self.url = "https://api.bcra.gob.ar/test"
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("sin json")
        return self._payload


def _fake_get_factory(script):
    """Devuelve un reemplazo de requests.get que recorre `script`.

    Cada elemento es una respuesta o una excepción a lanzar. El último elemento
    se repite si hay más llamadas que elementos.
    """
    calls = []

    def fake_get(url, params=None, timeout=None, verify=None, **kwargs):
        calls.append({"url": url, "params": params, "timeout": timeout, "verify": verify})
        item = script[min(len(calls) - 1, len(script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    return fake_get, calls


def _patch_sleep(monkeypatch, sleeps):
    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(bcra.time, "sleep", fake_sleep)


def test_reintenta_cortes_de_conexion_hasta_obtener_respuesta(monkeypatch):
    fake_get, calls = _fake_get_factory(
        [
            ConnectionError("Remote end closed connection without response"),
            ConnectionError("Remote end closed connection without response"),
            FakeResponse(200),
        ]
    )
    monkeypatch.setattr(bcra.requests, "get", fake_get)
    sleeps = []
    _patch_sleep(monkeypatch, sleeps)

    resp = bcra._request_bcra_json("/centraldedeudores/v1.0/Deudas/20374730429", max_retries=5)

    assert resp["http_status"] == 200
    assert resp["attempts"] == 3
    assert len(calls) == 3
    assert len(resp["retry_errors"]) == 2
    assert len(sleeps) == 2
    assert all(delay > 0 for delay in sleeps)


def test_reintenta_5xx_hasta_exito(monkeypatch):
    fake_get, calls = _fake_get_factory([FakeResponse(500), FakeResponse(200)])
    monkeypatch.setattr(bcra.requests, "get", fake_get)
    sleeps = []
    _patch_sleep(monkeypatch, sleeps)

    resp = bcra._request_bcra_json("/x", max_retries=3)

    assert resp["http_status"] == 200
    assert resp["attempts"] == 2
    assert len(calls) == 2
    assert resp["retry_errors"] == ["Intento 1: HTTP 500"]


def test_429_usa_backoff_mas_largo_que_los_cortes(monkeypatch):
    fake_get, _ = _fake_get_factory([FakeResponse(429, text="Too Many Requests"), FakeResponse(200)])
    monkeypatch.setattr(bcra.requests, "get", fake_get)
    sleeps = []
    _patch_sleep(monkeypatch, sleeps)

    resp = bcra._request_bcra_json("/x", max_retries=3)

    assert resp["http_status"] == 200
    assert resp["attempts"] == 2
    assert resp["rate_limited"] is True
    assert sleeps[0] >= bcra.BCRA_RATE_LIMIT_BACKOFF_BASE


def test_no_reintenta_404(monkeypatch):
    fake_get, calls = _fake_get_factory(
        [FakeResponse(404, payload={"status": 404, "errorMessages": ["No se encontró datos para la identificación ingresada."]})]
    )
    monkeypatch.setattr(bcra.requests, "get", fake_get)
    sleeps = []
    _patch_sleep(monkeypatch, sleeps)

    resp = bcra._request_bcra_json("/x", max_retries=10)

    assert resp["http_status"] == 404
    assert resp["attempts"] == 1
    assert len(calls) == 1
    assert sleeps == []


def test_agota_reintentos_y_devuelve_error_de_conexion(monkeypatch):
    fake_get, calls = _fake_get_factory([ConnectionError("Connection reset")])
    monkeypatch.setattr(bcra.requests, "get", fake_get)
    sleeps = []
    _patch_sleep(monkeypatch, sleeps)

    resp = bcra._request_bcra_json("/x", max_retries=2)

    assert resp["http_status"] is None
    assert resp["attempts"] == 3
    assert len(calls) == 3
    assert len(resp["retry_errors"]) == 2
    assert "Error de conexion" in resp["data"]["errorMessages"][0]
    assert len(sleeps) == 2


def test_ssl_fallback_intenta_sin_verificacion_en_el_mismo_intento(monkeypatch):
    fake_get, calls = _fake_get_factory([SSLError("certificate verify failed"), FakeResponse(200)])
    monkeypatch.setattr(bcra.requests, "get", fake_get)
    sleeps = []
    _patch_sleep(monkeypatch, sleeps)

    resp = bcra._request_bcra_json("/x")

    assert resp["http_status"] == 200
    assert resp["attempts"] == 1
    assert resp["ssl_verified"] is False
    assert calls[0]["verify"] is None
    assert calls[1]["verify"] is False
    assert sleeps == []


def test_las_consultas_son_secuenciales_aunque_se_invoquen_desde_varios_hilos(monkeypatch):
    activos = 0
    max_activos = 0
    contador_lock = threading.Lock()

    def fake_get(url, params=None, timeout=None, verify=None, **kwargs):
        nonlocal activos, max_activos
        with contador_lock:
            activos += 1
            max_activos = max(max_activos, activos)
        time_module.sleep(0.05)  # simula la latencia del servidor
        with contador_lock:
            activos -= 1
        return FakeResponse(200)

    monkeypatch.setattr(bcra.requests, "get", fake_get)

    hilos = [
        threading.Thread(target=bcra._request_bcra_json, args=("/x",), kwargs={"max_retries": 0})
        for _ in range(4)
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert max_activos == 1
