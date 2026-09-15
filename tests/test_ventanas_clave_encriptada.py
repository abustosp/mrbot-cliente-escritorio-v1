"""Verifica que las ventanas mandan la clave fiscal cifrada (clave_encriptada).

Maneja cada ventana real (camino individual y camino Excel) con la red mockeada:
la clave pública se reemplaza por un par RSA efímero, se captura el POST y se
comprueba que `clave_encriptada` descifra a la clave original y que ningún campo
en texto plano viaja al servidor.

Requiere un display (Tk): sin display los tests se saltean.
"""

import base64
import tkinter as tk
import unittest.mock as mock
from tkinter import messagebox

import pandas as pd
import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from mrbot_app import control_monotributistas
from mrbot_app import windows as W
from mrbot_app.seguridad import CAMPOS_CLAVE_PLANA

BASE_URL = "https://api-bots.mrbot.com.ar"
CONFIG = (BASE_URL, "api-key-test", "test@ejemplo.com")
CLAVE = "clave-secreta-de-prueba"
CUIT_REP = "20123456789"
CUIT_REPR = "30987654321"


def _tk_disponible() -> bool:
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _tk_disponible(), reason="requiere display para Tk"),
    # Tk avisa al recolectar widgets/variables fuera del mainloop; no afecta las aserciones.
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

CLAVE_PRIVADA = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# (etiqueta, clase, endpoint, metodo individual, tiene camino Excel)
CASOS = [
    ("mis_comprobantes", "GuiDescargaMC", "mis_comprobantes/consulta", "consulta_individual", True),
    ("sct", "SctWindow", "sct/consulta", "consulta_individual", True),
    ("ccma", "CcmaWindow", "ccma/consulta", "consulta_individual", True),
    ("rcel", "RcelWindow", "rcel/consulta", "consulta_individual", True),
    ("portal_iva", "PortalIvaWindow", "portal_iva/consulta", "consulta_individual", True),
    ("carga_iva_simple", "CargaIvaSimpleWindow", "portal_iva/carga", "carga_individual", True),
    ("hacienda", "HaciendaWindow", "hacienda/consulta", "consulta_individual", True),
    ("liquidacion_granos", "LiquidacionGranosWindow", "liquidacion_granos/consulta", "consulta_individual", True),
    ("mis_retenciones", "MisRetencionesWindow", "mis_retenciones/consulta", "consulta_individual", True),
    ("sifere", "SifereWindow", "sifere/consulta", "consulta_individual", True),
    ("mis_facilidades", "MisFacilidadesWindow", "mis_facilidades/consulta", "consulta_individual", True),
    ("declaracion_en_linea", "DeclaracionEnLineaWindow", "declaracion-en-linea/consulta", "consulta_individual", True),
    ("pago_devoluciones", "PagoDevolucionesWindow", "pago_devoluciones/consulta", "consulta_individual", True),
    ("aportes_en_linea", "AportesEnLineaWindow", "aportes-en-linea/consulta", "consulta_individual", True),
    ("srt_alicuotas", "SrtAlicuotasWindow", "srt/alicuotas/consulta", "consulta_individual", True),
    ("ret_per_arba", "RetPerArbaWindow", "arba/consulta", "consulta_individual", True),
    ("ret_per_agip", "RetPerAgipWindow", "agip/consulta", "consulta_individual", True),
    ("ret_per_misiones", "RetPerMisionesWindow", "misiones/consulta", "consulta_individual", True),
    ("vep_ccma", "VepCcmaWindow", "vep-ccma/generar", "consulta_individual", True),
    ("mis_retenciones_iva_simple", "MisRetencionesIvaSimpleWindow", "mis_retenciones_iva_simple/consulta", "consulta_individual", True),
]


class _RespuestaFake:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = "{}"

    def json(self):
        return self._payload


class _ConfigPaneFake:
    def get_config(self):
        return CONFIG


def _descifrar(cifrado_b64: str) -> str:
    return CLAVE_PRIVADA.decrypt(
        base64.b64decode(cifrado_b64),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    ).decode("utf-8")


def _sin_dialogos(*args, **kwargs):
    return False


@pytest.fixture(scope="module")
def red():
    """Mockea el transporte: clave pública propia y POSTs capturados."""
    enviados = []

    def fake_get(url, **kwargs):
        if "security/public-key" in url:
            return _RespuestaFake(200, {
                "key_id": "test",
                "algorithm": "RSA-OAEP-SHA256",
                "encoding": "base64",
                "public_key_pem": CLAVE_PRIVADA.public_key().public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                ).decode("ascii"),
            })
        raise AssertionError(f"GET inesperado durante el test: {url}")

    def fake_post(url, headers=None, json=None, data=None, files=None, params=None, timeout=None, **kwargs):
        enviados.append({"url": url, "json": json, "data": data})
        return _RespuestaFake(400, {"detail": {"message": ["error de prueba"], "error_code": "TEST"}})

    parches = [
        mock.patch.object(requests, "post", fake_post),
        mock.patch.object(requests, "get", fake_get),
    ] + [mock.patch.object(messagebox, nombre, _sin_dialogos) for nombre in ("showerror", "showwarning", "showinfo", "askyesno")]
    for parche in parches:
        parche.start()
    yield enviados
    for parche in parches:
        parche.stop()


@pytest.fixture(scope="module")
def raiz():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture(scope="module")
def df_excel(tmp_path_factory):
    descargas = str(tmp_path_factory.mktemp("descargas"))
    fila = {
        "procesar": "SI",
        "cuit_login": CUIT_REP,
        "cuit_representante": CUIT_REP,
        "cuit_representado": CUIT_REPR,
        "cuit": CUIT_REP,
        "representado_cuit": CUIT_REPR,
        "representado_nombre": "TEST",
        "denominacion": "TEST",
        "periodo": "202401",
        "periodo_desde": "202401",
        "periodo_hasta": "202401",
        "desde": "01/01/2024",
        "hasta": "31/01/2024",
        "clave": CLAVE,
        "clave_representante": CLAVE,
        "contrasena": CLAVE,
        "clave_fiscal": CLAVE,
        "usuario": "usuario-test",
        "cuits_consulta": CUIT_REP,
        "cuits": CUIT_REP,
        "descarga_mc": "SI",
        "descarga_mc_emitidos": "SI",
        "descarga_mc_recibidos": "SI",
        "descarga_rcel": "SI",
        "desde_mc": "01/01/2024",
        "hasta_mc": "31/01/2024",
        "desde_rcel": "01/01/2024",
        "hasta_rcel": "31/01/2024",
        "denominacion_mc": "TEST",
        "denominacion_rcel": "TEST",
        "ubicacion_descarga": descargas,
        "ubicacion_descarga_mc": descargas,
        "ubicacion_descarga_rcel": descargas,
    }
    return pd.DataFrame([fila])


def _preparar_ventana(win, descargas: str):
    for nombre in sorted(a for a in vars(win) if a.endswith("_var")):
        var = getattr(win, nombre)
        if nombre.startswith("_") or not hasattr(var, "set") or not isinstance(var.get(), str):
            continue
        bajo = nombre.lower()
        if "clave" in bajo:
            var.set(CLAVE)
        elif "cuit" in bajo and "repr" in bajo:
            var.set(CUIT_REPR)
        elif "cuit" in bajo:
            var.set(CUIT_REP)
        elif "usuario" in bajo:
            var.set("usuario-test")
        elif "periodo" in bajo:
            var.set("202401")
        elif "desde" in bajo:
            var.set("01/01/2024")
        elif "hasta" in bajo:
            var.set("31/01/2024")
        elif "denominacion" in bajo or "nombre" in bajo:
            var.set("TEST")
        elif "download" in bajo:
            var.set(descargas)

    for nombre, var in getattr(win, "_vars", {}).items():
        bajo = nombre.lower()
        if "clave" in bajo:
            var.set(CLAVE)
        elif "desde" in bajo:
            var.set("01/01/2024")
        elif "hasta" in bajo:
            var.set("31/01/2024")
        elif "periodo" in bajo:
            var.set("202401")
        elif "cuit" in bajo and "repr" in bajo:
            var.set(CUIT_REPR)
        elif "cuit" in bajo:
            var.set(CUIT_REP)
        elif "denominacion" in bajo or "nombre" in bajo:
            var.set("TEST")

    for atributo in ("cuits_text", "cuit_text", "text_cuits"):
        widget = getattr(win, atributo, None)
        if widget is not None and hasattr(widget, "insert"):
            widget.insert("1.0", f"{CUIT_REP},{CUIT_REPR}")

    def _sync(target, *args, **kwargs):
        try:
            target(*args, **kwargs)
        except Exception:
            pass

    # Captura de lo que la ventana escribe en el log (sin tocar el widget).
    logs_payload = []
    logs_texto = []
    win.logs_capturados = logs_payload
    win.logs_texto = logs_texto
    win.log_request = lambda payload, label="REQUEST": logs_payload.append(payload)
    win.log_request_started = lambda payload, **kwargs: logs_payload.append(payload)
    win.log_message = lambda message: logs_texto.append(str(message))

    win.run_in_thread = _sync
    return win


def _assert_clave_cifrada(enviados, endpoint):
    assert enviados, f"no se envio ningun request a {endpoint}"
    for envio in enviados:
        assert envio["url"].endswith(endpoint), f"endpoint inesperado: {envio['url']}"
        payload = envio["json"] if envio["json"] is not None else (envio["data"] or {})
        for campo in CAMPOS_CLAVE_PLANA:
            assert campo not in payload, f"{campo} viajo en texto plano a {endpoint}"
        assert "clave_encriptada" in payload, f"falta clave_encriptada en {endpoint}"
        assert _descifrar(payload["clave_encriptada"]) == CLAVE, f"la clave cifrada no descifra en {endpoint}"


def _cifrados_enviados(enviados):
    cifrados = set()
    for envio in enviados:
        payload = envio["json"] if envio["json"] is not None else (envio["data"] or {})
        if payload.get("clave_encriptada"):
            cifrados.add(payload["clave_encriptada"])
    return cifrados


def _assert_log_con_clave_encriptada(win, enviados):
    """El log debe mostrar el body enviado con clave_encriptada sin censurar."""
    cifrados = _cifrados_enviados(enviados)
    assert cifrados, "no se registro clave_encriptada en el envio"

    # Ventanas que loguean el payload directo (log_request / log_request_started).
    cuerpos = [p for p in getattr(win, "logs_capturados", []) if isinstance(p, dict)]
    for cuerpo in cuerpos:
        assert "clave_encriptada" in cuerpo, "el log no muestra clave_encriptada"
        assert cuerpo["clave_encriptada"] != "***", "el log censura clave_encriptada"
        assert cuerpo["clave_encriptada"] in cifrados, "el log no muestra el body enviado"
        for campo in CAMPOS_CLAVE_PLANA:
            assert campo not in cuerpo, f"el log muestra {campo} en texto plano"

    # Ventanas que delegan en un modulo que loguea por log_fn (self.log_message).
    texto = "\n".join(getattr(win, "logs_texto", []))
    assert CLAVE not in texto, "la clave fiscal quedo escrita en texto plano en el log"

    assert cuerpos or any(cifrado in texto for cifrado in cifrados), "el log no muestra el body enviado"


@pytest.mark.parametrize("etiqueta,clase_nombre,endpoint,metodo,tiene_excel", CASOS, ids=[c[0] for c in CASOS])
def test_ventana_manda_clave_cifrada(etiqueta, clase_nombre, endpoint, metodo, tiene_excel, raiz, red, df_excel, tmp_path):
    clase = getattr(W, clase_nombre)
    argumentos = {"config_pane": _ConfigPaneFake()} if clase_nombre == "GuiDescargaMC" else {"config_provider": lambda: CONFIG}
    win = clase(raiz, **argumentos)
    win.withdraw()
    try:
        _preparar_ventana(win, str(tmp_path))

        del red[:]
        getattr(win, metodo)()
        _assert_clave_cifrada(red, endpoint)
        _assert_log_con_clave_encriptada(win, red)

        if tiene_excel:
            del red[:]
            win.logs_capturados.clear()
            win.excel_df = df_excel
            win.procesar_excel()
            _assert_clave_cifrada(red, endpoint)
            _assert_log_con_clave_encriptada(win, red)
    finally:
        win.destroy()


def test_control_monotributistas_manda_clave_cifrada(red, df_excel):
    fila = df_excel.iloc[0]

    del red[:]
    control_monotributistas.procesar_descarga_mc(fila, log_fn=lambda m: None)
    _assert_clave_cifrada(red, "mis_comprobantes/consulta")

    del red[:]
    control_monotributistas.procesar_descarga_rcel(fila, CONFIG, log_fn=lambda m: None)
    _assert_clave_cifrada(red, "rcel/consulta")
