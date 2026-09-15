"""Regresión del menú principal: cada ventana usada debe estar importada y cableada."""

import re
from pathlib import Path

import mrbot

CODIGO = Path(mrbot.__file__).read_text(encoding="utf-8")


def test_ventanas_del_menu_estan_importadas():
    usadas = set(re.findall(r"\b(\w+Window)\(", CODIGO))

    assert usadas, "no se detectaron ventanas en mrbot.py"
    faltantes = sorted(nombre for nombre in usadas if not hasattr(mrbot, nombre))
    assert not faltantes, f"mrbot.py usa ventanas que no importa: {faltantes}"


def test_handlers_del_menu_tienen_boton():
    definidos = set(re.findall(r"def (open_\w+)\(", CODIGO))
    cableados = set(re.findall(r"command=self\.(open_\w+)", CODIGO))

    assert definidos == cableados, (
        f"handlers sin boton: {sorted(definidos - cableados)} | "
        f"botones sin handler: {sorted(cableados - definidos)}"
    )
