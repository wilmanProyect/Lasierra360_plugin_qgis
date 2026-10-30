# -*- coding: utf-8 -*-
"""NDVI Cobertura: superficie de suelo / baja / alta vegetación en ortomosaicos NDVI."""


def classFactory(iface):
    from .plugin import NdviCoberturaPlugin
    return NdviCoberturaPlugin(iface)
