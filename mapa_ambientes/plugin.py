# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtGui import QIcon
try:
    from qgis.PyQt.QtWidgets import QAction
except ImportError:  # Qt6 (QGIS 4)
    from qgis.PyQt.QtGui import QAction

MENU = "Mapa de Ambientes"


class MapaAmbientesPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.accion = None
        self.dialogo = None

    def initGui(self):
        icono = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.accion = QAction(icono, "Mapa de Ambientes (Zona Baja / Media / Alta)", self.iface.mainWindow())
        self.accion.triggered.connect(self.abrir)
        self.iface.addToolBarIcon(self.accion)
        self.iface.addPluginToMenu(MENU, self.accion)
        self.iface.addPluginToRasterMenu(MENU, self.accion)

    def unload(self):
        self.iface.removePluginMenu(MENU, self.accion)
        self.iface.removePluginRasterMenu(MENU, self.accion)
        self.iface.removeToolBarIcon(self.accion)
        if self.dialogo is not None:
            self.dialogo.close()
            self.dialogo = None

    def abrir(self):
        from .dialogo import DialogoAmbientes

        if self.dialogo is None:
            self.dialogo = DialogoAmbientes(self.iface, self.iface.mainWindow())
        self.dialogo.actualizar_capas()
        self.dialogo.show()
        self.dialogo.raise_()
        self.dialogo.activateWindow()

