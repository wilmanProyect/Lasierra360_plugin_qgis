# -*- coding: utf-8 -*-
"""Mapa de Ambientes: Superficie y proporción de Zona Baja / Media / Alta en mapas de ambientación."""


def classFactory(iface):
    from .plugin import MapaAmbientesPlugin
    return MapaAmbientesPlugin(iface)

