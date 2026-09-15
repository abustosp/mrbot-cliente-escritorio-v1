"""Cifrado en transito de la clave fiscal de los bots.

La credencial se cifra con la clave publica RSA que expone la API en
``/api/v1/security/public-key`` (RSA-OAEP-SHA256 + Base64) y se envia en el
campo ``clave_encriptada``. El texto plano nunca sale del cliente.
"""
from __future__ import annotations

import base64
import threading
import time
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from mrbot_app.config import get_public_key_cache_sec, get_request_timeouts


ENDPOINT_CLAVE_PUBLICA = "api/v1/security/public-key"
CAMPO_CLAVE_ENCRIPTADA = "clave_encriptada"
CAMPOS_CLAVE_PLANA = ("clave", "clave_representante", "contrasena")
ALGORITMO_ESPERADO = "RSA-OAEP-SHA256"
ENCODING_ESPERADO = "base64"


class ClaveEncriptacionError(RuntimeError):
    """No se pudo obtener la clave publica o cifrar la credencial."""


def base_url_de(url: str) -> str:
    """Devuelve ``scheme://host[:puerto]`` de una URL."""
    partes = urlsplit(str(url or ""))
    if not partes.scheme or not partes.netloc:
        raise ClaveEncriptacionError(f"URL invalida para cifrar la credencial: {url!r}")
    return f"{partes.scheme}://{partes.netloc}"


def _oaep() -> padding.OAEP:
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def _descargar_clave_publica(base_url: str):
    url = base_url.rstrip("/") + "/" + ENDPOINT_CLAVE_PUBLICA
    _, get_timeout = get_request_timeouts()

    try:
        respuesta = requests.get(url, timeout=get_timeout)
    except requests.RequestException as exc:
        raise ClaveEncriptacionError(f"No se pudo obtener la clave publica del servidor: {exc}") from exc

    if respuesta.status_code != 200:
        raise ClaveEncriptacionError(
            f"El servidor respondio HTTP {respuesta.status_code} al pedir la clave publica"
        )

    try:
        datos = respuesta.json()
    except ValueError as exc:
        raise ClaveEncriptacionError("La respuesta de la clave publica no es JSON") from exc

    if not isinstance(datos, Mapping):
        raise ClaveEncriptacionError("La respuesta de la clave publica tiene un formato inesperado")

    algoritmo = datos.get("algorithm")
    if algoritmo != ALGORITMO_ESPERADO:
        raise ClaveEncriptacionError(f"Algoritmo de cifrado no soportado: {algoritmo!r}")

    if str(datos.get("encoding") or "").lower() != ENCODING_ESPERADO:
        raise ClaveEncriptacionError(f"Encoding de cifrado no soportado: {datos.get('encoding')!r}")

    pem = datos.get("public_key_pem")
    if not isinstance(pem, str) or not pem.strip():
        raise ClaveEncriptacionError("La respuesta no incluye la clave publica PEM")

    try:
        clave = serialization.load_pem_public_key(pem.encode("utf-8"))
    except Exception as exc:
        raise ClaveEncriptacionError("No se pudo interpretar la clave publica recibida") from exc

    if not isinstance(clave, rsa.RSAPublicKey):
        raise ClaveEncriptacionError("La clave publica recibida no es RSA")

    return clave


class _CacheClavePublica:
    """Cache en memoria de la clave publica por host, con TTL y lock."""

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def obtener(self, base_url: str, force_refresh: bool = False):
        if not force_refresh:
            with self._lock:
                entrada = self._cache.get(base_url)
            if entrada is not None and (time.monotonic() - entrada[0]) < get_public_key_cache_sec():
                return entrada[1]

        clave = _descargar_clave_publica(base_url)
        with self._lock:
            self._cache[base_url] = (time.monotonic(), clave)
        return clave

    def limpiar(self, base_url: Optional[str] = None) -> None:
        with self._lock:
            if base_url is None:
                self._cache.clear()
            else:
                self._cache.pop(base_url, None)


_cache_clave_publica = _CacheClavePublica()


def obtener_clave_publica(base_url: str, force_refresh: bool = False):
    """Devuelve la clave publica RSA del servidor (cacheada)."""
    return _cache_clave_publica.obtener(base_url_de(base_url), force_refresh=force_refresh)


def invalidar_cache_clave_publica(base_url: Optional[str] = None) -> None:
    """Descarta la clave publica cacheada (por ejemplo si el servidor roto la clave)."""
    _cache_clave_publica.limpiar(base_url_de(base_url) if base_url else None)


def cifrar_clave(clave: str, base_url: str, force_refresh: bool = False) -> str:
    """Cifra una clave fiscal con la clave publica del servidor (Base64)."""
    if not isinstance(clave, str) or not clave.strip():
        raise ClaveEncriptacionError("La credencial debe ser texto no vacio")

    clave_publica = obtener_clave_publica(base_url, force_refresh=force_refresh)
    try:
        cifrado = clave_publica.encrypt(clave.encode("utf-8"), _oaep())
    except Exception as exc:
        raise ClaveEncriptacionError("No se pudo cifrar la credencial con la clave publica") from exc

    return base64.b64encode(cifrado).decode("ascii")


def preparar_payload(
    payload: Mapping[str, Any],
    base_url: str,
    clave: Optional[str] = None,
) -> Dict[str, Any]:
    """Devuelve una copia del payload con la credencial cifrada en ``clave_encriptada``.

    Nunca muta el payload original y nunca deja la credencial en texto plano.
    Si no hay credencial (o viene vacia) se elimina el campo plano y no se agrega
    ``clave_encriptada``.
    """
    datos: Dict[str, Any] = dict(payload)

    valor = clave
    if valor is None:
        for campo in CAMPOS_CLAVE_PLANA:
            if campo in datos:
                crudo = datos.get(campo)
                valor = "" if crudo is None else (crudo if isinstance(crudo, str) else str(crudo))
                break

    for campo in CAMPOS_CLAVE_PLANA:
        datos.pop(campo, None)

    if valor is None or not str(valor).strip():
        return datos

    datos[CAMPO_CLAVE_ENCRIPTADA] = cifrar_clave(str(valor), base_url)
    return datos


def tiene_clave_plana(payload: Mapping[str, Any]) -> bool:
    """Indica si el payload trae una credencial en texto plano."""
    return any(campo in payload for campo in CAMPOS_CLAVE_PLANA)
