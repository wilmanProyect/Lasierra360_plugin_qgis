# -*- coding: utf-8 -*-
"""Formato de números y filas del informe (compartido por el diálogo y el PDF)."""
from .analisis import ALTA, BAJA, SUELO


def fmt(valor, decimales=2):
    """Número con formato español: 1.234,56"""
    texto = "{:,.{}f}".format(valor, decimales)
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def fila_informe(nombre, areas_m2):
    """[nombre, total ha, suelo ha, %, baja ha, %, alta ha, %, vegetación ha, %]"""
    total = sum(areas_m2.values())

    def pct(a):
        return a / total * 100.0 if total else 0.0

    veg = areas_m2[BAJA] + areas_m2[ALTA]
    return [
        nombre, total / 1e4,
        areas_m2[SUELO] / 1e4, pct(areas_m2[SUELO]),
        areas_m2[BAJA] / 1e4, pct(areas_m2[BAJA]),
        areas_m2[ALTA] / 1e4, pct(areas_m2[ALTA]),
        veg / 1e4, pct(veg),
    ]
