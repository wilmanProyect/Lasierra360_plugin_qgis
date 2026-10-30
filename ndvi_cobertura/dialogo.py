# -*- coding: utf-8 -*-
import csv
import datetime
import html
import os
import tempfile

from osgeo import gdal
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsMapLayerProxyModel,
    QgsPalettedRasterRenderer,
    QgsProject,
    QgsRasterLayer,
    QgsSettings,
)
from qgis.gui import QgsFileWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QBrush, QColor, QDesktopServices
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import analisis, informe_pdf
from .analisis import ALTA, BAJA, CLASES, SUELO
from .formato import fila_informe, fmt

COLUMNAS = [
    "Ortomosaico",
    "Área total (ha)",
    "Suelo (ha)", "Suelo (%)",
    "Baja veg. (ha)", "Baja veg. (%)",
    "Alta veg. (ha)", "Alta veg. (%)",
    "Vegetación total (ha)", "Vegetación total (%)",
]
COLOR_COLUMNA = {2: "#d7191c", 3: "#d7191c", 4: "#fdd700", 5: "#fdd700",
                 6: "#1a9641", 7: "#1a9641"}


class DialogoCobertura(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.resultados = []
        self.parametros = {}
        self._cancelar = False
        self.setWindowTitle("NDVI Cobertura – Suelo / Baja / Alta vegetación")
        self.resize(980, 720)
        self._construir()

    # ------------------------------------------------------------------ UI
    def _construir(self):
        principal = QVBoxLayout(self)

        # Ortomosaicos
        grupo = QGroupBox("Ortomosaicos NDVI")
        lay = QVBoxLayout(grupo)
        self.lista = QListWidget()
        self.lista.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        lay.addWidget(self.lista)
        botones = QHBoxLayout()
        b_actualizar = QPushButton("Actualizar capas del proyecto")
        b_actualizar.clicked.connect(self.actualizar_capas)
        b_archivo = QPushButton("Agregar archivo(s)…")
        b_archivo.clicked.connect(self._agregar_archivos)
        b_todos = QPushButton("Marcar todos")
        b_todos.clicked.connect(lambda: self._marcar(True))
        b_ninguno = QPushButton("Desmarcar todos")
        b_ninguno.clicked.connect(lambda: self._marcar(False))
        for b in (b_actualizar, b_archivo, b_todos, b_ninguno):
            botones.addWidget(b)
        botones.addStretch()
        lay.addLayout(botones)
        principal.addWidget(grupo, 2)

        # Parámetros
        fila_param = QHBoxLayout()

        g_bandas = QGroupBox("Tipo de ortomosaico")
        f_bandas = QFormLayout(g_bandas)
        self.rb_rgb = QRadioButton("Imagen NDVI coloreada (RGB rojo-amarillo-verde)")
        self.rb_rgb.setToolTip("Ortomosaicos *.rgb.tif exportados por DJI Terra / SmartFarm u otros:\n"
                               "cada píxel se clasifica por su color.")
        self.rb_ndvi = QRadioButton("Raster NDVI (valores de NDVI)")
        self.rb_multi = QRadioButton("Raster multiespectral (calcular NDVI)")
        self.rb_rgb.setChecked(True)
        f_bandas.addRow(self.rb_rgb)
        self.sp_banda = QSpinBox()
        self.sp_banda.setRange(1, 999)
        self.sp_rojo = QSpinBox()
        self.sp_rojo.setRange(1, 999)
        self.sp_rojo.setValue(3)
        self.sp_nir = QSpinBox()
        self.sp_nir.setRange(1, 999)
        self.sp_nir.setValue(5)
        f_bandas.addRow(self.rb_ndvi)
        f_bandas.addRow("Banda NDVI:", self.sp_banda)
        f_bandas.addRow(self.rb_multi)
        f_bandas.addRow("Banda Rojo:", self.sp_rojo)
        f_bandas.addRow("Banda NIR:", self.sp_nir)
        fila_param.addWidget(g_bandas)

        g_umbral = QGroupBox("Umbrales de clasificación")
        f_umbral = QFormLayout(g_umbral)
        self.sp_bajo = self._spin_umbral(0.20)
        self.sp_alto = self._spin_umbral(0.50)
        self.lbl_bajo = self._etiqueta_color("#d7191c", "")
        self.lbl_alto = self._etiqueta_color("#1a9641", "")
        f_umbral.addRow(self.lbl_bajo, self.sp_bajo)
        f_umbral.addRow(self._etiqueta_color("#fdd700", "Baja vegetación (amarillo):  entre ambos"), QLabel(""))
        f_umbral.addRow(self.lbl_alto, self.sp_alto)
        self.lbl_ayuda = QLabel("")
        self.lbl_ayuda.setWordWrap(True)
        f_umbral.addRow(self.lbl_ayuda)
        self.chk_rango = QCheckBox("Ignorar valores fuera de [-1, 1] (fondo / sin dato)")
        self.chk_rango.setChecked(True)
        f_umbral.addRow(self.chk_rango)
        fila_param.addWidget(g_umbral)

        for rb in (self.rb_rgb, self.rb_ndvi, self.rb_multi):
            rb.toggled.connect(self._modo_bandas)
        self._modo_bandas()

        g_lim = QGroupBox("Opciones")
        f_lim = QFormLayout(g_lim)
        self.cb_poligono = QgsMapLayerComboBox()
        try:
            self.cb_poligono.setFilters(Qgis.LayerFilter.PolygonLayer)  # QGIS >= 3.34
        except AttributeError:
            self.cb_poligono.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.cb_poligono.setAllowEmptyLayer(True)
        self.cb_poligono.setLayer(None)
        self.chk_seleccion = QCheckBox("Sólo entidades seleccionadas")
        self.chk_clasificado = QCheckBox("Generar raster clasificado y agregarlo al mapa")
        self.chk_clasificado.setChecked(True)
        self.fw_carpeta = QgsFileWidget()
        self.fw_carpeta.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)
        self.fw_carpeta.setToolTip("Vacío = carpeta temporal")
        f_lim.addRow("Limitar a polígono (lote):", self.cb_poligono)
        f_lim.addRow("", self.chk_seleccion)
        f_lim.addRow(self.chk_clasificado)
        f_lim.addRow("Carpeta de salida:", self.fw_carpeta)
        fila_param.addWidget(g_lim)

        principal.addLayout(fila_param)

        # Ejecución
        fila_ejec = QHBoxLayout()
        self.b_calcular = QPushButton("Calcular")
        self.b_calcular.setDefault(True)
        self.b_calcular.clicked.connect(self.calcular)
        self.b_cancelar = QPushButton("Cancelar")
        self.b_cancelar.setEnabled(False)
        self.b_cancelar.clicked.connect(self._pedir_cancelar)
        self.progreso = QProgressBar()
        self.progreso.setRange(0, 1000)
        self.progreso.setValue(0)
        self.lbl_estado = QLabel("")
        fila_ejec.addWidget(self.b_calcular)
        fila_ejec.addWidget(self.b_cancelar)
        fila_ejec.addWidget(self.progreso, 1)
        principal.addLayout(fila_ejec)
        principal.addWidget(self.lbl_estado)

        # Resultados
        self.tabla = QTableWidget(0, len(COLUMNAS))
        self.tabla.setHorizontalHeaderLabels(COLUMNAS)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tabla.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        principal.addWidget(self.tabla, 3)

        fila_exp = QHBoxLayout()
        self.b_csv = QPushButton("Exportar CSV…")
        self.b_csv.clicked.connect(self.exportar_csv)
        self.b_pdf = QPushButton("Exportar informe PDF…")
        self.b_pdf.setStyleSheet("font-weight: bold")
        self.b_pdf.clicked.connect(self.exportar_pdf)
        self.b_html = QPushButton("Exportar informe HTML…")
        self.b_html.clicked.connect(self.exportar_html)
        self.b_copiar = QPushButton("Copiar tabla")
        self.b_copiar.clicked.connect(self.copiar)
        b_cerrar = QPushButton("Cerrar")
        b_cerrar.clicked.connect(self.close)
        for b in (self.b_pdf, self.b_csv, self.b_html, self.b_copiar):
            b.setEnabled(False)
            fila_exp.addWidget(b)
        fila_exp.addStretch()
        fila_exp.addWidget(b_cerrar)
        principal.addLayout(fila_exp)

    def _spin_umbral(self, valor):
        sp = QDoubleSpinBox()
        sp.setRange(-100000.0, 100000.0)
        sp.setDecimals(3)
        sp.setSingleStep(0.05)
        sp.setValue(valor)
        return sp

    def _etiqueta_color(self, color, texto):
        lbl = QLabel()
        lbl.setProperty("color", color)
        self._texto_etiqueta(lbl, texto)
        return lbl

    def _texto_etiqueta(self, lbl, texto):
        lbl.setText('<span style="background:{0};color:{0}">&nbsp;&nbsp;&nbsp;</span>&nbsp;{1}'
                    .format(lbl.property("color"), html.escape(texto)))

    def _modo(self):
        if self.rb_rgb.isChecked():
            return "rgb"
        return "multi" if self.rb_multi.isChecked() else "ndvi"

    def _modo_bandas(self, *args):
        modo = self._modo()
        if getattr(self, "_modo_actual", None) == modo:
            return
        self._modo_actual = modo
        self.sp_banda.setEnabled(modo == "ndvi")
        self.sp_rojo.setEnabled(modo == "multi")
        self.sp_nir.setEnabled(modo == "multi")
        self.chk_rango.setEnabled(modo != "rgb")
        if modo == "rgb":
            for sp in (self.sp_bajo, self.sp_alto):
                sp.setRange(-60.0, 180.0)
                sp.setDecimals(1)
                sp.setSingleStep(5.0)
                sp.setSuffix(" °")
            self.sp_bajo.setValue(30.0)
            self.sp_alto.setValue(90.0)
            self._texto_etiqueta(self.lbl_bajo, "Suelo (rojo):  tono <")
            self._texto_etiqueta(self.lbl_alto, "Alta vegetación (verde):  tono ≥")
            self.lbl_ayuda.setText("Tono del color: 0° = rojo, 60° = amarillo, 120° = verde. "
                                   "Con 30° / 90° cada píxel va al color más parecido. "
                                   "Lo transparente (fuera del vuelo) no se cuenta.")
        else:
            for sp in (self.sp_bajo, self.sp_alto):
                sp.setRange(-100000.0, 100000.0)
                sp.setDecimals(3)
                sp.setSingleStep(0.05)
                sp.setSuffix("")
            self.sp_bajo.setValue(0.20)
            self.sp_alto.setValue(0.50)
            self._texto_etiqueta(self.lbl_bajo, "Suelo (rojo):  NDVI <")
            self._texto_etiqueta(self.lbl_alto, "Alta vegetación (verde):  NDVI ≥")
            self.lbl_ayuda.setText("")

    # ------------------------------------------------------------ capas
    def actualizar_capas(self):
        marcados = {i["ruta"] for i in self._items(solo_marcados=True)}
        archivos = [i for i in self._items() if i["archivo"]]
        self.lista.clear()
        vistos = set()
        capas_proyecto = []
        for capa in QgsProject.instance().mapLayers().values():
            if isinstance(capa, QgsRasterLayer) and capa.providerType() == "gdal":
                ruta = capa.source()
                # se omiten los rasters clasificados que genera este plugin y las capas repetidas
                if "_clases_ndvi" in os.path.basename(ruta).lower() or os.path.normcase(ruta) in vistos:
                    continue
                vistos.add(os.path.normcase(ruta))
                capas_proyecto.append((capa.name(), ruta))
        # Ordenar capas del proyecto en orden ascendente por nombre
        for nombre, ruta in sorted(capas_proyecto, key=lambda x: x[0].lower()):
            marcar = ruta in marcados if marcados else "ndvi" in nombre.lower()
            self._agregar_item(nombre, ruta, marcar, archivo=False)
        # Ordenar archivos agregados manualmente en orden ascendente por nombre
        for i in sorted(archivos, key=lambda x: x["nombre"].lower()):
            self._agregar_item(i["nombre"], i["ruta"], i["ruta"] in marcados, archivo=True)

    def _agregar_item(self, nombre, ruta, marcado, archivo):
        item = QListWidgetItem(nombre if not archivo else "{}  (archivo)".format(nombre))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if marcado else Qt.CheckState.Unchecked)
        item.setToolTip(ruta)
        item.setData(Qt.ItemDataRole.UserRole, {"nombre": nombre, "ruta": ruta, "archivo": archivo})
        self.lista.addItem(item)

    def _agregar_archivos(self):
        rutas, _ = QFileDialog.getOpenFileNames(
            self, "Seleccionar ortomosaicos NDVI", "",
            "Raster (*.tif *.tiff *.vrt *.img *.jp2 *.ecw);;Todos (*.*)")
        existentes = {i["ruta"] for i in self._items()}
        for ruta in rutas:
            if ruta not in existentes:
                self._agregar_item(os.path.splitext(os.path.basename(ruta))[0], ruta, True, archivo=True)

    def _items(self, solo_marcados=False):
        res = []
        for i in range(self.lista.count()):
            item = self.lista.item(i)
            if solo_marcados and item.checkState() != Qt.CheckState.Checked:
                continue
            res.append(item.data(Qt.ItemDataRole.UserRole))
        return res

    def _marcar(self, estado):
        for i in range(self.lista.count()):
            self.lista.item(i).setCheckState(Qt.CheckState.Checked if estado else Qt.CheckState.Unchecked)

    def _geometrias(self, ruta, capa_vec, solo_seleccion):
        """Polígonos de la capa transformados al SRC del raster, en WKT."""
        ds = gdal.Open(ruta)
        proyeccion = ds.GetProjection() if ds is not None else ""
        ds = None
        crs_raster = QgsCoordinateReferenceSystem.fromWkt(proyeccion) if proyeccion else capa_vec.crs()
        transformar = QgsCoordinateTransform(capa_vec.crs(), crs_raster, QgsProject.instance())
        entidades = capa_vec.getSelectedFeatures() if solo_seleccion else capa_vec.getFeatures()
        wkts = []
        for f in entidades:
            g = QgsGeometry(f.geometry())
            if g.isNull() or g.isEmpty():
                continue
            g.transform(transformar)
            wkts.append(g.asWkt())
        return wkts

    # ------------------------------------------------------------ cálculo
    def _pedir_cancelar(self):
        self._cancelar = True

    def _ruta_salida(self, nombre):
        carpeta = self.fw_carpeta.filePath() or tempfile.gettempdir()
        base = "".join(c if c.isalnum() or c in "-_" else "_" for c in nombre)
        ruta = os.path.join(carpeta, "{}_clases_ndvi.tif".format(base))
        n = 2
        while os.path.exists(ruta):
            ruta = os.path.join(carpeta, "{}_clases_ndvi_{}.tif".format(base, n))
            n += 1
        return ruta

    def calcular(self):
        # Ordenar en orden ascendente por nombre para que la tabla y el informe salgan ordenados
        items = sorted(self._items(solo_marcados=True), key=lambda x: x["nombre"].lower())
        if not items:
            QMessageBox.warning(self, "NDVI Cobertura", "Marque al menos un ortomosaico.")
            return
        bajo, alto = self.sp_bajo.value(), self.sp_alto.value()
        if bajo >= alto:
            QMessageBox.warning(self, "NDVI Cobertura",
                                "El umbral de suelo debe ser menor que el de alta vegetación.")
            return
        capa_vec = self.cb_poligono.currentLayer()
        solo_sel = self.chk_seleccion.isChecked()
        if capa_vec is not None and solo_sel and capa_vec.selectedFeatureCount() == 0:
            QMessageBox.warning(self, "NDVI Cobertura",
                                "La capa '{}' no tiene entidades seleccionadas.".format(capa_vec.name()))
            return
        carpeta = self.fw_carpeta.filePath()
        if self.chk_clasificado.isChecked() and carpeta and not os.path.isdir(carpeta):
            QMessageBox.warning(self, "NDVI Cobertura", "La carpeta de salida no existe.")
            return

        modo = self._modo()
        origen = {
            "rgb": "Imagen NDVI coloreada (clasificación por color de cada píxel)",
            "ndvi": "Banda NDVI {}".format(self.sp_banda.value()),
            "multi": "NDVI calculado de bandas Rojo={} / NIR={}".format(self.sp_rojo.value(), self.sp_nir.value()),
        }[modo]
        self.parametros = {
            "modo": modo,
            "umbral_bajo": bajo,
            "umbral_alto": alto,
            "origen": origen,
            "poligono": ("{}{}".format(capa_vec.name(), " (selección)" if solo_sel else "")
                         if capa_vec is not None else ""),
            "fecha": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
        }
        self._capa_limite = capa_vec
        self.resultados = []
        errores = []
        self._cancelar = False
        self._ocupado(True)
        try:
            total = len(items)
            for indice, item in enumerate(items):
                if self._cancelar:
                    break
                self.lbl_estado.setText("Procesando {} ({}/{})…".format(item["nombre"], indice + 1, total))

                def progreso(fraccion, indice=indice):
                    self.progreso.setValue(int((indice + fraccion) / total * 1000))
                    QApplication.processEvents()
                    return self._cancelar

                try:
                    geoms = self._geometrias(item["ruta"], capa_vec, solo_sel) if capa_vec is not None else None
                    salida = self._ruta_salida(item["nombre"]) if self.chk_clasificado.isChecked() else None
                    res = analisis.analizar(
                        item["ruta"],
                        modo=modo,
                        banda=self.sp_banda.value(),
                        umbral_bajo=bajo,
                        umbral_alto=alto,
                        solo_rango_ndvi=self.chk_rango.isChecked(),
                        bandas_rojo_nir=(self.sp_rojo.value(), self.sp_nir.value()),
                        geometrias_wkt=geoms,
                        ruta_clasificado=salida,
                        progreso=progreso,
                    )
                except analisis.Cancelado:
                    break
                except Exception as e:  # se informa y se sigue con el resto
                    errores.append("{}: {}".format(item["nombre"], e))
                    continue
                res["nombre"] = item["nombre"]
                self.resultados.append(res)
                if salida:
                    self._cargar_clasificado(salida, item["nombre"])
        finally:
            self._ocupado(False)

        self._mostrar_tabla()
        if self._cancelar:
            self.lbl_estado.setText("Cancelado. Se muestran los ortomosaicos terminados.")
        else:
            self.progreso.setValue(1000)
            self.lbl_estado.setText(self._resumen())
        if errores:
            QMessageBox.warning(self, "NDVI Cobertura", "Algunos ortomosaicos no se procesaron:\n\n" + "\n".join(errores))

    def _ocupado(self, ocupado):
        self.b_calcular.setEnabled(not ocupado)
        self.b_cancelar.setEnabled(ocupado)
        if ocupado:
            self.progreso.setValue(0)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()

    def _cargar_clasificado(self, ruta, nombre):
        capa = QgsRasterLayer(ruta, "{} – clases NDVI".format(nombre))
        if not capa.isValid():
            return
        clases = [QgsPalettedRasterRenderer.Class(valor, QColor(color), etiqueta)
                  for valor, etiqueta, color in CLASES]
        capa.setRenderer(QgsPalettedRasterRenderer(capa.dataProvider(), 1, clases))
        QgsProject.instance().addMapLayer(capa)

    # ------------------------------------------------------------ informe
    def _filas(self):
        filas = [fila_informe(r["nombre"], r["areas_m2"]) for r in self.resultados]
        if len(self.resultados) > 1:
            suma = {c: sum(r["areas_m2"][c] for r in self.resultados) for c in (SUELO, BAJA, ALTA)}
            filas.append(fila_informe("TOTAL", suma))
        return filas

    def _resumen(self):
        if not self.resultados:
            return "Sin resultados."
        f = self._filas()[-1]
        texto = "Área total: {} ha  |  Suelo {} %  |  Baja veg. {} %  |  Alta veg. {} %  |  Vegetación total {} ha ({} %)".format(
            fmt(f[1]), fmt(f[3], 1), fmt(f[5], 1), fmt(f[7], 1), fmt(f[8]), fmt(f[9], 1))
        avisos = []
        if any(r["geografico"] for r in self.resultados):
            avisos.append("raster en coordenadas geográficas: área aproximada (esfera)")
        if any(r["sin_src"] for r in self.resultados):
            avisos.append("raster sin SRC: se asumieron metros")
        if avisos:
            texto += "\nAviso: " + "; ".join(avisos)
        return texto

    def _mostrar_tabla(self):
        filas = self._filas()
        self.tabla.setRowCount(len(filas))
        for r, fila in enumerate(filas):
            negrita = fila[0] == "TOTAL" and r == len(filas) - 1 and len(self.resultados) > 1
            for c, valor in enumerate(fila):
                if c == 0:
                    texto = valor
                elif c % 2 == 1 and c != 1:
                    texto = fmt(valor, 1)
                else:
                    texto = fmt(valor)
                celda = QTableWidgetItem(texto)
                if c > 0:
                    celda.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if c in COLOR_COLUMNA:
                    color = QColor(COLOR_COLUMNA[c])
                    color.setAlpha(60)
                    celda.setBackground(QBrush(color))
                if negrita:
                    fuente = celda.font()
                    fuente.setBold(True)
                    celda.setFont(fuente)
                self.tabla.setItem(r, c, celda)
        hay = bool(filas)
        for b in (self.b_pdf, self.b_csv, self.b_html, self.b_copiar):
            b.setEnabled(hay)

    def _texto_filas(self):
        filas = []
        for fila in self._filas():
            filas.append([fila[0]] + [fmt(v, 2) for v in fila[1:]])
        return filas

    def copiar(self):
        lineas = ["\t".join(COLUMNAS)] + ["\t".join(f) for f in self._texto_filas()]
        QApplication.clipboard().setText("\n".join(lineas))
        self.lbl_estado.setText("Tabla copiada al portapapeles (pegar en Excel).")

    def exportar_csv(self):
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar CSV", "informe_ndvi.csv", "CSV (*.csv)")
        if not ruta:
            return
        with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(COLUMNAS)
            w.writerows(self._texto_filas())
            w.writerow([])
            w.writerow(["Fecha", self.parametros["fecha"]])
            w.writerow(["Origen", self.parametros["origen"]])
            rangos = self._rangos()
            for valor, etiqueta, _ in CLASES:
                w.writerow([etiqueta, rangos[valor]])
            if self.parametros["poligono"]:
                w.writerow(["Límite", self.parametros["poligono"]])
        self.lbl_estado.setText("CSV guardado: {}".format(ruta))

    def _capas_fondo(self):
        """Mapas base visibles en el lienzo (Google, OSM, WMS…) para usar de fondo en los mapas del PDF."""
        try:
            capas = self.iface.mapCanvas().layers()
        except AttributeError:
            return []
        return [c for c in capas if isinstance(c, QgsRasterLayer) and c.providerType() in ("wms", "xyz", "arcgismapserver")]

    def exportar_pdf(self):
        datos = DialogoDatosInforme.pedir(self)
        if datos is None:
            return
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar informe PDF", "Informe_cobertura_NDVI.pdf", "PDF (*.pdf)")
        if not ruta:
            return
        if not ruta.lower().endswith(".pdf"):
            ruta += ".pdf"

        def progreso(texto):
            self.lbl_estado.setText(texto)
            QApplication.processEvents()

        self._ocupado(True)
        self.b_cancelar.setEnabled(False)
        try:
            paginas = informe_pdf.generar_pdf(
                ruta, self.resultados, self.parametros, self._rangos(), datos,
                capas_fondo=self._capas_fondo(), capa_limite=getattr(self, "_capa_limite", None),
                fichas=datos["fichas"], progreso=progreso)
        except Exception as e:
            self._ocupado(False)
            QMessageBox.critical(self, "NDVI Cobertura", "No se pudo generar el PDF:\n{}".format(e))
            return
        self._ocupado(False)
        self.lbl_estado.setText("Informe PDF guardado ({} páginas): {}".format(paginas, ruta))
        QDesktopServices.openUrl(QUrl.fromLocalFile(ruta))

    def exportar_html(self):
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar informe", "informe_ndvi.html", "HTML (*.html)")
        if not ruta:
            return
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(self._html())
        QDesktopServices.openUrl(QUrl.fromLocalFile(ruta))
        self.lbl_estado.setText("Informe guardado: {}".format(ruta))

    def _rangos(self):
        p = self.parametros
        if p["modo"] == "rgb":
            b, a = fmt(p["umbral_bajo"], 0) + "°", fmt(p["umbral_alto"], 0) + "°"
            return {
                SUELO: "Color rojo/naranja (tono < {})".format(b),
                BAJA: "Color amarillo (tono {} – {})".format(b, a),
                ALTA: "Color verde (tono ≥ {})".format(a),
            }
        b, a = fmt(p["umbral_bajo"], 2), fmt(p["umbral_alto"], 2)
        return {
            SUELO: "NDVI < {}".format(b),
            BAJA: "{} ≤ NDVI < {}".format(b, a),
            ALTA: "NDVI ≥ {}".format(a),
        }

    def _html(self):
        p = self.parametros
        rangos = {k: html.escape(v) for k, v in self._rangos().items()}
        bloques = []
        filas = self._filas()
        for fila in filas:
            nombre = html.escape(fila[0])
            barra = "".join(
                '<div style="width:{:.3f}%;background:{}"></div>'.format(fila[3 + 2 * i], color)
                for i, (_, _, color) in enumerate(CLASES))
            cuerpo = "".join(
                "<tr><td><span class='sw' style='background:{}'></span>{}</td><td>{}</td>"
                "<td class='n'>{}</td><td class='n'>{} %</td></tr>".format(
                    color, etiqueta, rangos[valor], fmt(fila[2 + 2 * i]), fmt(fila[3 + 2 * i], 1))
                for i, (valor, etiqueta, color) in enumerate(CLASES))
            bloques.append(
                "<section><h2>{nombre}</h2><div class='barra'>{barra}</div>"
                "<table><thead><tr><th>Clase</th><th>Rango</th><th class='n'>Superficie (ha)</th>"
                "<th class='n'>%</th></tr></thead><tbody>{cuerpo}</tbody><tfoot>"
                "<tr><td colspan='2'>Vegetación total (baja + alta)</td><td class='n'>{veg}</td><td class='n'>{vegp} %</td></tr>"
                "<tr><td colspan='2'>Área total analizada</td><td class='n'>{tot}</td><td class='n'>100 %</td></tr>"
                "</tfoot></table></section>".format(
                    nombre=nombre, barra=barra, cuerpo=cuerpo, veg=fmt(fila[8]), vegp=fmt(fila[9], 1),
                    tot=fmt(fila[1])))

        detalle = "".join(
            "<tr><td>{}</td><td>{}</td><td class='n'>{} × {}</td><td class='n'>{}</td></tr>".format(
                html.escape(r["nombre"]), html.escape(r["ruta"]),
                fmt(r["resolucion"][0], 4), fmt(r["resolucion"][1], 4), fmt(r["pixeles"], 0))
            for r in self.resultados)

        return """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Informe de cobertura NDVI</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:32px auto;max-width:900px;color:#222;padding:0 16px}}
h1{{margin-bottom:4px}} .meta{{color:#666;margin-bottom:24px}}
section{{border:1px solid #ddd;border-radius:8px;padding:16px 20px;margin-bottom:20px}}
h2{{margin:0 0 12px;font-size:1.15em}}
table{{border-collapse:collapse;width:100%}} th,td{{padding:6px 8px;border-bottom:1px solid #eee;text-align:left}}
th{{background:#f5f5f5}} .n{{text-align:right}} tfoot td{{font-weight:600}}
.sw{{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:8px;vertical-align:middle}}
.barra{{display:flex;height:18px;border-radius:4px;overflow:hidden;margin-bottom:12px;background:#eee}}
.det td{{font-size:.85em;color:#555;word-break:break-all}}
</style></head><body>
<h1>Informe de cobertura NDVI</h1>
<div class="meta">Fecha: {fecha} · Origen: {origen}{limite}</div>
{bloques}
<h3>Detalle técnico</h3>
<table class="det"><thead><tr><th>Ortomosaico</th><th>Archivo</th><th class="n">Tamaño de píxel</th><th class="n">Píxeles válidos</th></tr></thead>
<tbody>{detalle}</tbody></table>
<p class="meta">{avisos}</p>
</body></html>""".format(
            fecha=p["fecha"], origen=html.escape(p["origen"]),
            limite=" · Límite: " + html.escape(p["poligono"]) if p["poligono"] else "",
            bloques="".join(bloques), detalle=detalle,
            avisos=html.escape(self._resumen().split("\n", 1)[1]) if "\n" in self._resumen() else "")


class DialogoDatosInforme(QDialog):
    """Datos de portada del informe PDF; se recuerdan entre usos."""
    CAMPOS = (
        ("proyecto", "Proyecto:"),
        ("cliente", "Cliente / Propiedad:"),
        ("fecha_vuelo", "Fecha de vuelo:"),
        ("elaborado", "Elaborado por:"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Datos del informe PDF")
        self.setMinimumWidth(460)
        ajustes = QgsSettings()
        form = QFormLayout(self)
        self.editores = {}
        for clave, etiqueta in self.CAMPOS:
            ed = QLineEdit(ajustes.value("ndvi_cobertura/" + clave, "", type=str))
            self.editores[clave] = ed
            form.addRow(etiqueta, ed)
        if not self.editores["proyecto"].text():
            proyecto = QgsProject.instance()
            self.editores["proyecto"].setText(proyecto.title() or proyecto.baseName())
        self.editores["fecha_vuelo"].setPlaceholderText("p. ej. 25/08/2026")
        self.chk_fichas = QCheckBox("Incluir una ficha con mapa por cada lote")
        self.chk_fichas.setChecked(ajustes.value("ndvi_cobertura/fichas", True, type=bool))
        form.addRow(self.chk_fichas)
        botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        form.addRow(botones)

    def datos(self):
        d = {clave: ed.text().strip() for clave, ed in self.editores.items()}
        d["fichas"] = self.chk_fichas.isChecked()
        ajustes = QgsSettings()
        for clave, valor in d.items():
            ajustes.setValue("ndvi_cobertura/" + clave, valor)
        return d

    @classmethod
    def pedir(cls, parent):
        dlg = cls(parent)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.datos()
