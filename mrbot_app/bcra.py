import math
import random
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import requests
from requests.exceptions import SSLError
from urllib3.exceptions import InsecureRequestWarning


BCRA_BASE_URL = "https://api.bcra.gob.ar"
DEFAULT_BCRA_TIMEOUT = 60

# Reintentos ante errores transitorios del BCRA (cortes de conexion, 429, 5xx).
# Las consultas son siempre secuenciales: _BCRA_REQUEST_LOCK serializa los requests
# aunque el modulo se use desde varios hilos.
DEFAULT_BCRA_RETRIES = 10
BCRA_RETRY_BACKOFF_BASE = 0.8
BCRA_RETRY_BACKOFF_MAX = 5.0
BCRA_RETRY_JITTER = 0.4
BCRA_RATE_LIMIT_BACKOFF_BASE = 5.0
BCRA_RATE_LIMIT_BACKOFF_MAX = 20.0
BCRA_RATE_LIMIT_JITTER = 1.0
_BCRA_REQUEST_LOCK = threading.Lock()

# Definicion de operaciones soportadas segun OpenAPI oficial del BCRA.
BCRA_OPERATIONS: Dict[str, Dict[str, Any]] = {
    "central_deudores_deudas": {
        "label": "Central de Deudores - Deudas",
        "group": "Central de Deudores",
        "path": "/centraldedeudores/v1.0/Deudas/{identificacion}",
        "required": ["identificacion"],
        "optional": [],
        "path_params": ["identificacion"],
        "query_params": {},
        "example_file": "bcra_central_deudores.xlsx",
    },
    "central_deudores_historicas": {
        "label": "Central de Deudores - Historicas",
        "group": "Central de Deudores",
        "path": "/centraldedeudores/v1.0/Deudas/Historicas/{identificacion}",
        "required": ["identificacion"],
        "optional": [],
        "path_params": ["identificacion"],
        "query_params": {},
        "example_file": "bcra_central_deudores.xlsx",
    },
    "central_deudores_cheques_rechazados": {
        "label": "Central de Deudores - Cheques Rechazados",
        "group": "Central de Deudores",
        "path": "/centraldedeudores/v1.0/Deudas/ChequesRechazados/{identificacion}",
        "required": ["identificacion"],
        "optional": [],
        "path_params": ["identificacion"],
        "query_params": {},
        "example_file": "bcra_central_deudores.xlsx",
    },
    "cheques_entidades": {
        "label": "Cheques Denunciados - Entidades",
        "group": "Cheques Denunciados",
        "path": "/cheques/v1.0/entidades",
        "required": [],
        "optional": [],
        "path_params": [],
        "query_params": {},
        "example_file": "bcra_cheques_denunciados.xlsx",
    },
    "cheques_denunciados": {
        "label": "Cheques Denunciados - Consulta de Cheque",
        "group": "Cheques Denunciados",
        "path": "/cheques/v1.0/denunciados/{codigo_entidad}/{numero_cheque}",
        "required": ["codigo_entidad", "numero_cheque"],
        "optional": [],
        "path_params": ["codigo_entidad", "numero_cheque"],
        "query_params": {},
        "example_file": "bcra_cheques_denunciados.xlsx",
    },
    "cambiarias_divisas": {
        "label": "Estadisticas Cambiarias - Divisas",
        "group": "Estadisticas Cambiarias",
        "path": "/estadisticascambiarias/v1.0/Maestros/Divisas",
        "required": [],
        "optional": [],
        "path_params": [],
        "query_params": {},
        "example_file": "bcra_estadisticas_cambiarias.xlsx",
    },
    "cambiarias_cotizaciones": {
        "label": "Estadisticas Cambiarias - Cotizaciones",
        "group": "Estadisticas Cambiarias",
        "path": "/estadisticascambiarias/v1.0/Cotizaciones",
        "required": [],
        "optional": ["fecha"],
        "path_params": [],
        "query_params": {"fecha": "fecha"},
        "example_file": "bcra_estadisticas_cambiarias.xlsx",
    },
    "cambiarias_cotizacion_moneda": {
        "label": "Estadisticas Cambiarias - Cotizacion por Moneda",
        "group": "Estadisticas Cambiarias",
        "path": "/estadisticascambiarias/v1.0/Cotizaciones/{cod_moneda}",
        "required": ["cod_moneda"],
        "optional": ["fecha_desde", "fecha_hasta", "limit", "offset"],
        "path_params": ["cod_moneda"],
        "query_params": {
            "fecha_desde": "fechaDesde",
            "fecha_hasta": "fechaHasta",
            "limit": "limit",
            "offset": "offset",
        },
        "example_file": "bcra_estadisticas_cambiarias.xlsx",
    },
    "monetarias_metodologia": {
        "label": "Estadisticas Monetarias - Metodologia",
        "group": "Estadisticas Monetarias",
        "path": "/estadisticas/v4.0/Metodologia",
        "required": [],
        "optional": ["limit", "offset"],
        "path_params": [],
        "query_params": {"limit": "Limit", "offset": "Offset"},
        "example_file": "bcra_estadisticas_monetarias.xlsx",
    },
    "monetarias_metodologia_variable": {
        "label": "Estadisticas Monetarias - Metodologia por Variable",
        "group": "Estadisticas Monetarias",
        "path": "/estadisticas/v4.0/Metodologia/{id_variable}",
        "required": ["id_variable"],
        "optional": [],
        "path_params": ["id_variable"],
        "query_params": {},
        "example_file": "bcra_estadisticas_monetarias.xlsx",
    },
    "monetarias_monetarias": {
        "label": "Estadisticas Monetarias - Variables",
        "group": "Estadisticas Monetarias",
        "path": "/estadisticas/v4.0/Monetarias",
        "required": [],
        "optional": [
            "id_variable",
            "categoria",
            "periodicidad",
            "moneda",
            "tipo_serie",
            "unidad_expresion",
            "limit",
            "offset",
        ],
        "path_params": [],
        "query_params": {
            "id_variable": "IdVariable",
            "categoria": "Categoria",
            "periodicidad": "Periodicidad",
            "moneda": "Moneda",
            "tipo_serie": "TipoSerie",
            "unidad_expresion": "UnidadExpresion",
            "limit": "Limit",
            "offset": "Offset",
        },
        "example_file": "bcra_estadisticas_monetarias.xlsx",
    },
    "monetarias_variable": {
        "label": "Estadisticas Monetarias - Serie de Variable",
        "group": "Estadisticas Monetarias",
        "path": "/estadisticas/v4.0/Monetarias/{id_variable}",
        "required": ["id_variable"],
        "optional": ["desde", "hasta", "limit", "offset"],
        "path_params": ["id_variable"],
        "query_params": {
            "desde": "Desde",
            "hasta": "Hasta",
            "limit": "Limit",
            "offset": "Offset",
        },
        "example_file": "bcra_estadisticas_monetarias.xlsx",
    },
}

_INT_FIELDS = {
    "identificacion",
    "codigo_entidad",
    "numero_cheque",
    "id_variable",
    "limit",
    "offset",
}

_UPPER_FIELDS = {
    "cod_moneda",
    "periodicidad",
    "moneda",
}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return not stripped or stripped.lower() == "nan"
    if isinstance(value, float):
        return math.isnan(value)
    return False


def _to_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Parametro invalido '{name}': no puede ser booleano.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"Parametro invalido '{name}': se esperaba entero.")
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"Parametro invalido '{name}': se esperaba entero.") from exc


def _normalize_param(field: str, value: Any) -> Any:
    if field in _INT_FIELDS:
        return _to_int(value, field)
    text = str(value).strip()
    if field in _UPPER_FIELDS:
        text = text.upper()
    return text


def _build_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _retry_delay(attempt: int, rate_limited: bool) -> float:
    if rate_limited:
        base = min(BCRA_RATE_LIMIT_BACKOFF_BASE * attempt, BCRA_RATE_LIMIT_BACKOFF_MAX)
        return base + random.uniform(0, BCRA_RATE_LIMIT_JITTER)
    base = min(BCRA_RETRY_BACKOFF_BASE * attempt, BCRA_RETRY_BACKOFF_MAX)
    return base + random.uniform(0, BCRA_RETRY_JITTER)


def _is_retryable_status(status: Any) -> bool:
    return status == 429 or (isinstance(status, int) and status >= 500)


def _connection_error_result(
    url: str,
    message: str,
    ssl_verified: bool,
    attempts: int,
    retry_errors: List[str],
    rate_limited: bool = False,
) -> Dict[str, Any]:
    return {
        "http_status": None,
        "url": url,
        "data": {
            "status": None,
            "errorMessages": [message],
        },
        "ssl_verified": ssl_verified,
        "attempts": attempts,
        "rate_limited": rate_limited,
        "retry_errors": list(retry_errors),
    }


def _request_bcra_json(
    path: str,
    query_params: Optional[Dict[str, Any]] = None,
    base_url: str = BCRA_BASE_URL,
    timeout_sec: int = DEFAULT_BCRA_TIMEOUT,
    allow_insecure_fallback: bool = True,
    max_retries: int = DEFAULT_BCRA_RETRIES,
) -> Dict[str, Any]:
    url = _build_url(base_url, path)
    params = query_params or None
    max_attempts = max(1, int(max_retries) + 1)

    used_ssl_verification = True
    attempts = 0
    retry_errors: List[str] = []
    rate_limited = False

    # El lock garantiza que las consultas al BCRA se hagan siempre de a una,
    # aunque el modulo se invoque desde varios hilos.
    with _BCRA_REQUEST_LOCK:
        resp = None
        while attempts < max_attempts:
            attempts += 1
            try:
                resp = requests.get(url, params=params, timeout=timeout_sec)
            except SSLError as exc:
                if not allow_insecure_fallback:
                    return _connection_error_result(
                        url, f"Error SSL: {exc}", used_ssl_verification, attempts, retry_errors
                    )

                requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
                used_ssl_verification = False
                try:
                    resp = requests.get(
                        url,
                        params=params,
                        timeout=timeout_sec,
                        verify=False,
                    )
                except Exception as retry_exc:
                    if attempts < max_attempts:
                        retry_errors.append(f"Intento {attempts}: {retry_exc}")
                        time.sleep(_retry_delay(attempts, rate_limited))
                        continue
                    return _connection_error_result(
                        url,
                        f"Error de conexion: {retry_exc}",
                        used_ssl_verification,
                        attempts,
                        retry_errors,
                        rate_limited,
                    )
            except Exception as exc:
                if attempts < max_attempts:
                    retry_errors.append(f"Intento {attempts}: {exc}")
                    time.sleep(_retry_delay(attempts, rate_limited))
                    continue
                return _connection_error_result(
                    url,
                    f"Error de conexion: {exc}",
                    used_ssl_verification,
                    attempts,
                    retry_errors,
                    rate_limited,
                )

            if _is_retryable_status(resp.status_code):
                if resp.status_code == 429:
                    rate_limited = True
                if attempts < max_attempts:
                    retry_errors.append(f"Intento {attempts}: HTTP {resp.status_code}")
                    time.sleep(_retry_delay(attempts, resp.status_code == 429))
                    continue
            break

    try:
        payload = resp.json()
    except ValueError:
        payload = {
            "status": resp.status_code,
            "errorMessages": ["Respuesta no JSON."],
            "raw_text": resp.text,
        }

    return {
        "http_status": resp.status_code,
        "url": resp.url,
        "data": payload,
        "ssl_verified": used_ssl_verification,
        "attempts": attempts,
        "rate_limited": rate_limited,
        "retry_errors": retry_errors,
    }


def _prepare_operation_params(operation: str, params: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    spec = BCRA_OPERATIONS.get(operation)
    if spec is None:
        valid = ", ".join(BCRA_OPERATIONS.keys())
        raise ValueError(f"Operacion no soportada: {operation}. Operaciones disponibles: {valid}")

    source = params or {}
    prepared: Dict[str, Any] = {}

    missing: List[str] = []
    for field in spec.get("required", []):
        value = source.get(field)
        if _is_empty(value):
            missing.append(field)
            continue
        prepared[field] = _normalize_param(field, value)

    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Faltan parametros requeridos para '{operation}': {joined}")

    for field in spec.get("optional", []):
        value = source.get(field)
        if _is_empty(value):
            continue
        prepared[field] = _normalize_param(field, value)

    return spec, prepared


def _is_empty_cotizaciones_payload(data: Any) -> bool:
    if not isinstance(data, dict):
        return True
    results = data.get("results")
    if not isinstance(results, dict):
        return True
    fecha = str(results.get("fecha") or "").strip()
    detalle = results.get("detalle")
    return (not fecha) and (isinstance(detalle, list) and len(detalle) == 0)


def _resolve_previous_cotizacion_date(
    requested_date: str,
    base_url: str,
    timeout_sec: int,
    allow_insecure_fallback: bool,
    max_retries: int = DEFAULT_BCRA_RETRIES,
) -> Tuple[Optional[str], Optional[str]]:
    # Se consulta USD como moneda de referencia para ubicar la ultima fecha habil <= requested_date.
    lookup_resp = _request_bcra_json(
        path="/estadisticascambiarias/v1.0/Cotizaciones/USD",
        query_params={
            "fechaDesde": "1992-01-02",
            "fechaHasta": requested_date,
            "limit": 10,
            "offset": 0,
        },
        base_url=base_url,
        timeout_sec=timeout_sec,
        allow_insecure_fallback=allow_insecure_fallback,
        max_retries=max_retries,
    )
    lookup_warning = lookup_resp.get("ssl_warning")
    lookup_data = lookup_resp.get("data")
    if not isinstance(lookup_data, dict):
        return None, lookup_warning
    results = lookup_data.get("results")
    if not isinstance(results, list):
        return None, lookup_warning
    for item in results:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("fecha") or "").strip()
        if candidate:
            return candidate, lookup_warning
    return None, lookup_warning


def run_bcra_operation(
    operation: str,
    params: Optional[Dict[str, Any]] = None,
    base_url: str = BCRA_BASE_URL,
    timeout_sec: int = DEFAULT_BCRA_TIMEOUT,
    allow_insecure_fallback: bool = True,
    max_retries: int = DEFAULT_BCRA_RETRIES,
) -> Dict[str, Any]:
    spec, prepared = _prepare_operation_params(operation, params)

    path_args = {
        name: quote(str(prepared[name]), safe="")
        for name in spec.get("path_params", [])
        if name in prepared
    }
    path = spec["path"].format(**path_args)

    query_params: Dict[str, Any] = {}
    for source_name, api_name in spec.get("query_params", {}).items():
        if source_name in prepared:
            query_params[api_name] = prepared[source_name]

    response = _request_bcra_json(
        path=path,
        query_params=query_params,
        base_url=base_url,
        timeout_sec=timeout_sec,
        allow_insecure_fallback=allow_insecure_fallback,
        max_retries=max_retries,
    )

    if operation == "cambiarias_cotizaciones" and "fecha" in prepared and _is_empty_cotizaciones_payload(response.get("data")):
        requested_date = str(prepared["fecha"])
        available_date, lookup_warning = _resolve_previous_cotizacion_date(
            requested_date=requested_date,
            base_url=base_url,
            timeout_sec=timeout_sec,
            allow_insecure_fallback=allow_insecure_fallback,
            max_retries=max_retries,
        )
        if available_date and available_date != requested_date:
            retry_resp = _request_bcra_json(
                path=path,
                query_params={"fecha": available_date},
                base_url=base_url,
                timeout_sec=timeout_sec,
                allow_insecure_fallback=allow_insecure_fallback,
                max_retries=max_retries,
            )
            retry_resp["date_adjustment_warning"] = (
                f"No hubo cotizaciones para {requested_date}. "
                f"Se consulto automaticamente la ultima fecha disponible: {available_date}."
            )
            retry_resp["requested_params"] = dict(prepared)
            retry_resp["effective_params"] = {"fecha": available_date}
            if lookup_warning:
                retry_resp["lookup_ssl_warning"] = lookup_warning
            response = retry_resp
        elif lookup_warning:
            response["lookup_ssl_warning"] = lookup_warning

    response["operation"] = operation
    response["request_params"] = prepared
    return response


def get_bcra_operation_choices() -> List[Tuple[str, str]]:
    return [(op_id, spec.get("label", op_id)) for op_id, spec in BCRA_OPERATIONS.items()]


def get_example_excel_name_for_operation(operation: str) -> Optional[str]:
    spec = BCRA_OPERATIONS.get(operation)
    if not spec:
        return None
    return spec.get("example_file")


def extract_bcra_error_messages(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return []
    raw = data.get("errorMessages")
    if raw is None:
        raw = data.get("errors")
    if raw is None:
        raw = data.get("errores")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, dict):
        return [str(raw)]
    text = str(raw).strip()
    return [text] if text else []


def _iter_or_empty(value: Any) -> Iterable[Any]:
    return value if isinstance(value, list) else []


def _append_rows_or_base(rows: List[Dict[str, Any]], base: Dict[str, Any], detail: Any) -> None:
    if isinstance(detail, dict):
        combined = dict(base)
        combined.update(detail)
        rows.append(combined)
        return
    rows.append(dict(base))


def _flatten_central_deudores(data: Dict[str, Any], history: bool = False) -> List[Dict[str, Any]]:
    results = data.get("results")
    if not isinstance(results, dict):
        return []
    base = {
        "identificacion": results.get("identificacion"),
        "denominacion": results.get("denominacion"),
    }
    rows: List[Dict[str, Any]] = []

    for periodo in _iter_or_empty(results.get("periodos")):
        if not isinstance(periodo, dict):
            continue
        base_periodo = dict(base)
        base_periodo["periodo"] = periodo.get("periodo")
        entidades = _iter_or_empty(periodo.get("entidades"))
        if not entidades:
            rows.append(base_periodo)
            continue
        for entidad in entidades:
            if isinstance(entidad, dict):
                row = dict(base_periodo)
                row.update(entidad)
                rows.append(row)
            else:
                rows.append(dict(base_periodo))

    if rows:
        return rows
    if history:
        return [base] if base["identificacion"] is not None else []
    return [base] if base["identificacion"] is not None else []


def _flatten_central_cheques_rechazados(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = data.get("results")
    if not isinstance(results, dict):
        return []

    base = {
        "identificacion": results.get("identificacion"),
        "denominacion": results.get("denominacion"),
    }
    rows: List[Dict[str, Any]] = []

    for causal in _iter_or_empty(results.get("causales")):
        if not isinstance(causal, dict):
            continue
        causal_name = causal.get("causal")
        entidades = _iter_or_empty(causal.get("entidades"))
        if not entidades:
            row = dict(base)
            row["causal"] = causal_name
            rows.append(row)
            continue
        for entidad in entidades:
            if not isinstance(entidad, dict):
                row = dict(base)
                row["causal"] = causal_name
                rows.append(row)
                continue
            entidad_code = entidad.get("entidad")
            detalle = _iter_or_empty(entidad.get("detalle"))
            if not detalle:
                row = dict(base)
                row["causal"] = causal_name
                row["entidad"] = entidad_code
                rows.append(row)
                continue
            for item in detalle:
                row = dict(base)
                row["causal"] = causal_name
                row["entidad"] = entidad_code
                if isinstance(item, dict):
                    row.update(item)
                rows.append(row)

    return rows or ([base] if base["identificacion"] is not None else [])


def _flatten_cheques_denunciados(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = data.get("results")
    if not isinstance(results, dict):
        return []

    base = {
        "numeroCheque": results.get("numeroCheque"),
        "denunciado": results.get("denunciado"),
        "fechaProcesamiento": results.get("fechaProcesamiento"),
        "denominacionEntidad": results.get("denominacionEntidad"),
    }
    rows: List[Dict[str, Any]] = []
    details = _iter_or_empty(results.get("detalles"))

    if not details:
        return [base]

    for detail in details:
        _append_rows_or_base(rows, base, detail)
    return rows


def _flatten_cotizaciones_results(results: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(results, dict):
        fecha = results.get("fecha")
        detalle = _iter_or_empty(results.get("detalle"))
        if not detalle:
            return [{"fecha": fecha}] if fecha else []
        for item in detalle:
            row = {"fecha": fecha}
            if isinstance(item, dict):
                row.update(item)
            rows.append(row)
        return rows

    if isinstance(results, list):
        for block in results:
            if not isinstance(block, dict):
                continue
            fecha = block.get("fecha")
            detalle = _iter_or_empty(block.get("detalle"))
            if not detalle:
                if fecha:
                    rows.append({"fecha": fecha})
                continue
            for item in detalle:
                row = {"fecha": fecha}
                if isinstance(item, dict):
                    row.update(item)
                rows.append(row)
    return rows


def _flatten_monetarias_variable(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in _iter_or_empty(data.get("results")):
        if not isinstance(item, dict):
            continue
        base = {"idVariable": item.get("idVariable")}
        detalle = _iter_or_empty(item.get("detalle"))
        if not detalle:
            rows.append(base)
            continue
        for serie in detalle:
            row = dict(base)
            if isinstance(serie, dict):
                row.update(serie)
            rows.append(row)
    return rows


def flatten_bcra_results(operation: str, data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        return []

    if operation in {"central_deudores_deudas", "central_deudores_historicas"}:
        return _flatten_central_deudores(data, history=operation.endswith("historicas"))

    if operation == "central_deudores_cheques_rechazados":
        return _flatten_central_cheques_rechazados(data)

    if operation == "cheques_entidades":
        results = data.get("results")
        return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []

    if operation == "cheques_denunciados":
        return _flatten_cheques_denunciados(data)

    if operation in {"cambiarias_divisas", "monetarias_metodologia", "monetarias_metodologia_variable", "monetarias_monetarias"}:
        results = data.get("results")
        return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []

    if operation in {"cambiarias_cotizaciones", "cambiarias_cotizacion_moneda"}:
        return _flatten_cotizaciones_results(data.get("results"))

    if operation == "monetarias_variable":
        return _flatten_monetarias_variable(data)

    return []
