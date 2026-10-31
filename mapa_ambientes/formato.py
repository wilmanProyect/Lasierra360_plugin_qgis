# -*- coding: utf-8 -*-
"""Formato de números y filas del informe de ambientes."""
from .analisis import ALTA, BAJA, MEDIA


def fmt(valor, decimales=2):
    """Número con formato español: 1.234,56"""
    texto = "{:,.{}f}".format(valor, decimales)
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def fila_informe(nombre, areas_m2):
    """[nombre, total ha, baja ha, %, media ha, %, alta ha, %]"""
    total = sum(areas_m2.values())

    def pct(a):
        return a / total * 100.0 if total else 0.0

    return [
        nombre,
        total / 1e4,
        areas_m2[BAJA] / 1e4, pct(areas_m2[BAJA]),
        areas_m2[MEDIA] / 1e4, pct(areas_m2[MEDIA]),
        areas_m2[ALTA] / 1e4, pct(areas_m2[ALTA]),
    ]

