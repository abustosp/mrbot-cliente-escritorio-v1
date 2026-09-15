import calendar
import concurrent.futures
import json
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import tkinter as tk
from tkinter import messagebox, ttk

from mrbot_app.config import get_max_workers
from mrbot_app.helpers import (
    build_headers,
    df_preview,
    ensure_trailing_slash,
    format_date_str,
    parse_bool_cell,
    safe_post,
)
from mrbot_app.windows.base import BaseWindow
from mrbot_app.windows.mixins import DownloadHandlerMixin, ExcelHandlerMixin


IVA_SIMPLE_OPERACIONES = (
    "RETENCIONES IMPOSITIVAS",
    "PERCEPCIONES IMPOSITIVAS",
    "PERCEPCIONES ADUANERAS",
)


def iva_simple_max_hasta(desde: str) -> Optional[date]:
    try:
        value = datetime.strptime(format_date_str(desde), "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def validate_iva_simple_date_range(desde: str, hasta: str) -> Optional[str]:
    max_hasta = iva_simple_max_hasta(desde)
    try:
        desde_date = datetime.strptime(format_date_str(desde), "%d/%m/%Y").date()
        hasta_date = datetime.strptime(format_date_str(hasta), "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return "Las fechas deben tener formato DD/MM/AAAA."
    if hasta_date < desde_date:
        return "La fecha Hasta no puede ser anterior a Desde."
    if max_hasta and hasta_date > max_hasta:
        return f"Para IVA Simple, Hasta no puede superar {max_hasta.strftime('%d/%m/%Y')}."
    return None


class MisRetencionesIvaSimpleWindow(BaseWindow, ExcelHandlerMixin, DownloadHandlerMixin):
    MODULE_DIR = "Mis_Retenciones_IVA_Simple"

    def __init__(self, master=None, config_provider=None, example_paths: Optional[Dict[str, str]] = None):
        super().__init__(master, title="Mis Retenciones IVA Simple", config_provider=config_provider)
        ExcelHandlerMixin.__init__(self)
        DownloadHandlerMixin.__init__(self)
        self.example_paths = example_paths or {}
        try:
            self.iconbitmap(os.path.join("bin", "ABP-blanco-en-fondo-negro.ico"))
        except Exception:
            pass

        container = ttk.Frame(self, padding=10)
        container.pack(fill="both", expand=True)
        self.add_section_label(container, "Mis Retenciones IVA Simple")
        self.add_info_label(
            container,
            "Descarga las tres operaciones de ARCA en formato IVA Simple y XLS. "
            "Consulta individual o masiva (Excel). "
            "Hasta no puede superar el último día del mes siguiente a Desde. "
            "Columnas del Excel: procesar, cuit_representante, clave_representante, cuit_representado, "
            "denominacion, desde, hasta, ubicacion_descarga, proxy_request, retry.",
        )

        inputs = ttk.Frame(container)
        inputs.pack(fill="x", pady=4)
        fields = (
            ("CUIT representante", "cuit_representante", False),
            ("Clave representante", "clave_representante", True),
            ("CUIT representado (opcional)", "cuit_representado", False),
            ("Denominación", "denominacion", False),
            ("Fecha desde (DD/MM/AAAA)", "desde", False),
            ("Fecha hasta (DD/MM/AAAA)", "hasta", False),
        )
        self._vars: Dict[str, tk.StringVar] = {}
        for row, (label, name, secret) in enumerate(fields):
            ttk.Label(inputs, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=2)
            variable = tk.StringVar()
            self._vars[name] = variable
            ttk.Entry(inputs, textvariable=variable, width=30, show="*" if secret else "").grid(
                row=row, column=1, padx=4, pady=2, sticky="ew"
            )
        inputs.columnconfigure(1, weight=1)

        opts = ttk.Frame(container)
        opts.pack(fill="x", pady=2)
        self.proxy_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="proxy_request", variable=self.proxy_var).pack(side="left", padx=4)

        self.add_download_path_frame(container)
        buttons = ttk.Frame(container)
        buttons.pack(fill="x", pady=4)
        ttk.Button(buttons, text="Consultar individual", command=self.consulta_individual).grid(
            row=0, column=0, padx=4, pady=2, sticky="ew"
        )
        ttk.Button(buttons, text="Seleccionar Excel", command=self.cargar_excel).grid(
            row=0, column=1, padx=4, pady=2, sticky="ew"
        )
        ttk.Button(
            buttons,
            text="Ejemplo Excel",
            command=lambda: self.abrir_ejemplo_key("mis_retenciones_iva_simple.xlsx"),
        ).grid(row=0, column=2, padx=4, pady=2, sticky="ew")
        ttk.Button(
            buttons,
            text="Previsualizar Excel",
            command=lambda: self.previsualizar_excel("Previsualizacion Mis Retenciones IVA Simple"),
        ).grid(row=0, column=3, padx=4, pady=2, sticky="ew")
        ttk.Button(buttons, text="Procesar Excel", command=self.procesar_excel).grid(
            row=1, column=0, columnspan=4, padx=4, pady=6, sticky="ew"
        )
        buttons.columnconfigure((0, 1, 2, 3), weight=1)

        self.preview = self.add_preview(container, height=8, show=False)
        self.result_box = self.add_preview(container, height=14)
        self.set_preview(self.preview, "Excel no cargado o sin previsualizar. Usa 'Previsualizar Excel'.")

        self.progress_frame = self.add_progress_bar(container, label="Progreso")

        self.log_text = self.add_collapsible_log(
            container, title="Logs de ejecución", height=12, service="mis_retenciones_iva_simple"
        )

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def append_log(self, text: str) -> None:
        if not text:
            return
        self.log_message(text)

    def _value(self, name: str) -> str:
        return self._vars[name].get().strip()

    def consulta_individual(self) -> None:
        desde = format_date_str(self._value("desde"))
        hasta = format_date_str(self._value("hasta"))
        date_error = validate_iva_simple_date_range(desde, hasta)
        if date_error:
            messagebox.showerror("Rango inválido", date_error)
            return

        base_url, api_key, email = self._get_config()
        payload = {
            "cuit_representante": self._value("cuit_representante"),
            "clave_representante": self._vars["clave_representante"].get(),
            "cuit_representado": self._value("cuit_representado") or None,
            "denominacion": self._value("denominacion"),
            "desde": desde,
            "hasta": hasta,
            "carga_minio": True,
            "proxy_request": bool(self.proxy_var.get()),
        }
        url = ensure_trailing_slash(base_url) + "api/v1/mis_retenciones_iva_simple/consulta"
        headers = build_headers(api_key, email)
        self.clear_logs()
        self.run_in_thread(
            self.run_with_log_block,
            payload["cuit_representado"] or payload["cuit_representante"] or "sin_cuit",
            self._worker_individual,
            url,
            headers,
            payload,
        )

    def _worker_individual(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> None:
        self.log_start("Mis Retenciones IVA Simple", {"modo": "individual", "operaciones": list(IVA_SIMPLE_OPERACIONES)})
        self.log_separator(payload.get("cuit_representado") or payload.get("cuit_representante"))
        payload = self.cifrar_payload(payload, url)
        if payload is None:
            return
        self.log_request_started(payload)
        response = safe_post(url, headers, payload)
        data = response.get("data", {}) if isinstance(response, dict) else {}
        self.log_response_finished(response.get("http_status") if isinstance(response, dict) else None, data)
        cuit_folder = payload.get("cuit_representado") or payload.get("cuit_representante") or "sin_cuit"
        downloads, errors, download_dir = self._process_downloads(
            data, self.MODULE_DIR, cuit_folder, service_key="retencion"
        )
        if downloads:
            self.log_info(f"Descargas completadas: {downloads} -> {download_dir}")
        elif data:
            self.log_info("Sin links de descarga en la respuesta.")
        for error in errors:
            self.log_error(f"Descarga: {error}")
        self.set_preview(self.result_box, json.dumps(response, indent=2, ensure_ascii=False))

    def procesar_excel(self) -> None:
        if self.excel_df is None or self.excel_df.empty:
            self.set_progress(0, 0)
            messagebox.showerror("Error", "Carga un Excel primero.")
            return

        df_to_process = self._filter_procesar(self.excel_df)
        if df_to_process is None or df_to_process.empty:
            self.set_progress(0, 0)
            messagebox.showwarning("Sin filas a procesar", "No hay filas marcadas con procesar=SI.")
            return

        base_url, api_key, email = self._get_config()
        headers = build_headers(api_key, email)
        url = ensure_trailing_slash(base_url) + "api/v1/mis_retenciones_iva_simple/consulta"

        default_desde = format_date_str(self._value("desde"))
        default_hasta = format_date_str(self._value("hasta"))
        default_proxy = bool(self.proxy_var.get())

        df_copy = df_to_process.copy()

        self.clear_logs()
        self.log_start(
            "Mis Retenciones IVA Simple",
            {"modo": "masivo", "filas": len(df_copy), "operaciones": list(IVA_SIMPLE_OPERACIONES)},
        )

        self.run_in_thread(
            self._worker_excel,
            df_copy,
            url,
            headers,
            default_desde,
            default_hasta,
            default_proxy,
        )

    def _worker_excel(self, df, url, headers, default_desde, default_hasta, default_proxy):
        rows: List[Dict[str, Any]] = []
        total = len(df)
        self.set_progress(0, total)
        max_workers = get_max_workers()

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self.run_with_log_block,
                    str(row.get("cuit_representado", "")).strip()
                    or str(row.get("cuit_representante", "")).strip()
                    or "sin_cuit",
                    self._process_row_iva_simple,
                    row,
                    url,
                    headers,
                    default_desde,
                    default_hasta,
                    default_proxy,
                ): idx
                for idx, (_, row) in enumerate(df.iterrows(), start=1)
            }

            completed = 0
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                completed += 1
                if self._abort_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                try:
                    result = future.result()
                    if result:
                        rows.append(result)
                except Exception as exc:
                    self.log_error(f"Error en fila {idx}: {exc}")

                self.set_progress(completed, total)

        out_df = pd.DataFrame(rows)
        self.set_preview(self.result_box, df_preview(out_df, rows=min(20, len(out_df))))
        self.set_execution_summary(
            self.build_download_execution_summary("Mis Retenciones IVA Simple", rows, total_expected=total)
        )
        self.log_info("Procesamiento masivo finalizado.")

    def _process_row_iva_simple(self, row, url, headers, default_desde, default_hasta, default_proxy):
        if self._abort_event.is_set():
            return None

        cuit_rep = str(row.get("cuit_representante", "")).strip()
        cuit_repr = str(row.get("cuit_representado", "")).strip()
        denominacion = str(row.get("denominacion", "")).strip()
        desde = format_date_str(row.get("desde", "")) or default_desde
        hasta = format_date_str(row.get("hasta", "")) or default_hasta
        proxy_request = None
        if "proxy_request" in row.index:
            proxy_request = parse_bool_cell(row.get("proxy_request"), default=default_proxy)
        row_download = str(
            row.get("ubicacion_descarga")
            or row.get("path_descarga")
            or row.get("carpeta_descarga")
            or ""
        ).strip()
        self.log_separator(cuit_repr or cuit_rep)

        date_error = validate_iva_simple_date_range(desde, hasta)
        if date_error:
            self.log_error(date_error)
            return {
                "cuit_representado": cuit_repr or cuit_rep,
                "http_status": None,
                "success": False,
                "message": date_error,
                "descarga_esperada": True,
                "descargas": 0,
                "errores_descarga": None,
                "carpeta_descarga": None,
            }

        payload: Dict[str, Any] = {
            "cuit_representante": cuit_rep,
            "clave_representante": str(row.get("clave_representante", "")),
            "cuit_representado": cuit_repr or None,
            "denominacion": denominacion,
            "desde": desde,
            "hasta": hasta,
            "carga_minio": True,
        }
        if proxy_request is not None:
            payload["proxy_request"] = proxy_request
        payload = self.cifrar_payload(payload, url)
        if payload is None:
            return None

        try:
            retry_val = int(row.get("retry", 0))
        except (ValueError, TypeError):
            retry_val = 0
        total_attempts = retry_val if retry_val > 1 else 1

        resp: Dict[str, Any] = {}
        data: Dict[str, Any] = {}
        for attempt in range(1, total_attempts + 1):
            self.log_request_started(payload, attempt=attempt, total_attempts=total_attempts)
            resp = safe_post(url, headers, payload)
            data = resp.get("data", {}) if isinstance(resp, dict) else {}
            self.log_response_finished(resp.get("http_status"), data)
            if resp.get("http_status") == 200:
                break

        downloads, errors, download_dir = self._process_downloads(
            data,
            self.MODULE_DIR,
            cuit_repr or cuit_rep,
            override_dir=row_download,
            service_key="retencion",
        )
        if downloads:
            self.log_info(f"Descargas completadas: {downloads} -> {download_dir}")
        elif data:
            self.log_info("Sin links de descarga en la respuesta.")
        for error in errors:
            self.log_error(f"Descarga: {error}")

        return {
            "cuit_representado": cuit_repr or cuit_rep,
            "http_status": resp.get("http_status"),
            "success": data.get("success") if isinstance(data, dict) else None,
            "message": data.get("message") if isinstance(data, dict) else None,
            "descarga_esperada": True,
            "descargas": downloads,
            "errores_descarga": "; ".join(errors) if errors else None,
            "carpeta_descarga": download_dir,
        }


__all__ = ["IVA_SIMPLE_OPERACIONES", "MisRetencionesIvaSimpleWindow", "iva_simple_max_hasta", "validate_iva_simple_date_range"]
