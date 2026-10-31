# -*- coding: utf-8 -*-
"""Formato de números y filas del informe (compartido por el diálogo y el PDF)."""
from .analisis import ALTA, BAJA, SUELO


def fmt(valor, decimales=2):
    """Número con formato español: 1.234,56"""
    texto = "{:,.{}f}".format(valor, decimales)
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def fila_informe(nombre, areas_m2, rinde_params=None):
    """[nombre, total ha, suelo ha, %, baja ha, %, alta ha, %, vegetación ha, %, (rinde_prom, prod_total)]"""
    total = sum(areas_m2.values())

    def pct(a):
        return a / total * 100.0 if total else 0.0

    veg = areas_m2[BAJA] + areas_m2[ALTA]
    fila = [
        nombre, total / 1e4,
        areas_m2[SUELO] / 1e4, pct(areas_m2[SUELO]),
        areas_m2[BAJA] / 1e4, pct(areas_m2[BAJA]),
        areas_m2[ALTA] / 1e4, pct(areas_m2[ALTA]),
        veg / 1e4, pct(veg),
    ]
    if rinde_params and rinde_params.get("habilitado"):
        ha_suelo = areas_m2[SUELO] / 1e4
        ha_baja = areas_m2[BAJA] / 1e4
        ha_alta = areas_m2[ALTA] / 1e4
        ha_tot = total / 1e4
        r_suelo = float(rinde_params.get("rinde_suelo", 0.0))
        r_baja = float(rinde_params.get("rinde_baja", 0.0))
        r_alta = float(rinde_params.get("rinde_alta", 0.0))
        prod = (ha_suelo * r_suelo) + (ha_baja * r_baja) + (ha_alta * r_alta)
        rinde_prom = (prod / ha_tot) if ha_tot > 0 else 0.0
        fila.extend([rinde_prom, prod])
    return fila
