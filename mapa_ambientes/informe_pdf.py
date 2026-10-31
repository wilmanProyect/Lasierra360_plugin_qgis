# -*- coding: utf-8 -*-
"""Informe PDF formal de Ambientes: logo LaSierra 360, mapas, tabla y guía agronómica.

El documento se maqueta con QTextDocument (HTML) y se pagina sobre un QPdfWriter
para dibujar encabezado y pie de página en cada hoja. Cada ficha de lote se procesa
de forma individual para optimizar el consumo de memoria.
"""
import html
import math
import os
import re

from qgis.core import (
    QgsColorRampShader,
    QgsCoordinateTransform,
    QgsFillSymbol,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsPalettedRasterRenderer,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsRectangle,
    QgsSingleBandPseudoColorRenderer,
    QgsSingleSymbolRenderer,
)
from qgis.PyQt.QtCore import QMarginsF, QPointF, QRectF, QSize, QSizeF, Qt, QUrl
from qgis.PyQt.QtGui import (
    QColor,
    QFont,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPdfWriter,
    QPen,
    QPolygonF,
    QTextDocument,
)

from .analisis import ALTA, BAJA, CLASES, MEDIA
from .formato import fila_informe, fmt

COLOR_OSCURO = "#0e3511"
COLOR_MARCA = "#7dc300"
TINTE = {BAJA: "#fbe1e1", MEDIA: "#fff5c2", ALTA: "#dff1e3"}
COLOR = {valor: color for valor, _, color in CLASES}
NOMBRE = {valor: etiqueta for valor, etiqueta, _ in CLASES}
COLOR_TEXTO = {BAJA: "#ffffff", MEDIA: "#3a3000", ALTA: "#ffffff"}

DPI = 300
LOGO = os.path.join(os.path.dirname(__file__), "logo.png")

CSS = """
h1 { color: %(o)s; font-size: 22pt; font-weight: bold; margin-bottom: 0pt; }
h2 { color: %(o)s; font-size: 14pt; font-weight: bold; margin-top: 12pt; margin-bottom: 6pt; }
h3 { color: %(o)s; font-size: 11pt; font-weight: bold; margin-top: 10pt; margin-bottom: 4pt; }
p { font-size: 10pt; color: #222222; margin-bottom: 6pt; }
li { font-size: 10pt; color: #222222; margin-bottom: 3pt; }
td { font-size: 9pt; color: #222222; }
th { font-size: 8.5pt; color: #ffffff; background-color: %(o)s; font-weight: bold; }
.nota { font-size: 8.5pt; color: #555555; }
.sub { font-size: 11pt; color: #4d6b3a; }
""" % {"o": COLOR_OSCURO}

ESPACIO = '<p style="font-size:6pt">&nbsp;</p>'


def _mm(mm):
    return mm * DPI / 25.4


def nombre_corto(nombre):
    """'CLAS LOTE A12.tif' -> 'LOTE A12'"""
    nombre = re.sub(r"\.(tif|tiff|img|jp2|rgb)$", "", nombre.strip(), flags=re.IGNORECASE)
    return re.sub(r"^(CLAS|AMB|AMBIENTE)\s*[-_]?\s*", "", nombre, flags=re.IGNORECASE)


# ---------------------------------------------------------------- mapas
def _aplicar_estilo_ambientes(capa, res=None):
    """Garantiza la visualización de los 3 colores (Rojo = Baja, Amarillo = Media, Verde = Alta)."""
    if not capa or not capa.isValid():
        return capa

    modo = (res or {}).get("modo", "clases")
    val_baja = (res or {}).get("val_baja", 1)
    val_media = (res or {}).get("val_media", 2)
    val_alta = (res or {}).get("val_alta", 3)

    fcn = QgsColorRampShader()
    if modo == "clases":
        fcn.setColorRampType(QgsColorRampShader.Type.Exact)
        items = [
            QgsColorRampShader.ColorRampItem(float(val_baja), QColor("#d7191c"), "Zona Baja"),
            QgsColorRampShader.ColorRampItem(float(val_media), QColor("#fdd700"), "Zona Media"),
            QgsColorRampShader.ColorRampItem(float(val_alta), QColor("#1a9641"), "Zona Alta"),
        ]
    else:  # continuo / umbrales
        u_bajo = (res or {}).get("umbral_bajo", 100.0)
        u_alto = (res or {}).get("umbral_alto", 105.0)
        fcn.setColorRampType(QgsColorRampShader.Type.Discrete)
        items = [
            QgsColorRampShader.ColorRampItem(float(u_bajo), QColor("#d7191c"), "Zona Baja"),
            QgsColorRampShader.ColorRampItem(float(u_alto), QColor("#fdd700"), "Zona Media"),
            QgsColorRampShader.ColorRampItem(float(u_alto + 99999.0), QColor("#1a9641"), "Zona Alta"),
        ]
    fcn.setColorRampItemList(items)
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(fcn)
    renderer = QgsSingleBandPseudoColorRenderer(capa.dataProvider(), 1, shader)
    renderer.setClassificationMin(min(float(val_baja), 1.0))
    renderer.setClassificationMax(max(float(val_alta), 3.0))
    capa.setRenderer(renderer)
    return capa


def capa_para_mapa(res):
    """Obtiene la capa de ambientes para el mapa garantizando la simbología de 3 colores."""
    # 1. Buscar primero en el proyecto activo si existe una capa con ese nombre o ruta
    proyecto = QgsProject.instance()
    for l in proyecto.mapLayers().values():
        if isinstance(l, QgsRasterLayer) and l.isValid():
            nombre_l = l.name().lower()
            res_nom = res.get("nombre", "").lower()
            fuente_l = os.path.normcase(l.source())
            res_ruta = os.path.normcase(res.get("ruta", ""))
            if nombre_l == res_nom or (fuente_l and fuente_l == res_ruta):
                clon = l.clone()
                if clon and clon.isValid():
                    # Si ya tiene un renderer pseudocolor o paletted configurado, conservarlo
                    rend = clon.renderer()
                    if rend and rend.type() in ("singlebandpseudocolor", "paletted"):
                        return clon
                    return _aplicar_estilo_ambientes(clon, res)

    # 2. Si hay ruta clasificada generada
    ruta_clas = res.get("ruta_clasificado")
    if ruta_clas and os.path.exists(ruta_clas):
        capa = QgsRasterLayer(ruta_clas, res["nombre"])
        if capa.isValid():
            return _aplicar_estilo_ambientes(capa, res)

    # 3. Capa original desde disco
    ruta_orig = res.get("ruta")
    if ruta_orig and os.path.exists(ruta_orig):
        capa = QgsRasterLayer(ruta_orig, res["nombre"])
        if capa.isValid():
            return _aplicar_estilo_ambientes(capa, res)

    return None


def capa_contorno(capa_vec):
    """Copia de la capa de polígonos dibujada sólo con contorno oscuro."""
    if capa_vec is None:
        return None
    copia = capa_vec.clone()
    simbolo = QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": "30,30,30", "outline_width": "0.4"})
    copia.setRenderer(QgsSingleSymbolRenderer(simbolo))
    return copia


def renderizar_mapa(capas, capas_extension, fondo, contorno, ancho, alto):
    proyecto = QgsProject.instance()
    crs = proyecto.crs() if proyecto.crs().isValid() else capas_extension[0].crs()
    extension = QgsRectangle()
    extension.setMinimal()
    for capa in capas_extension:
        tr = QgsCoordinateTransform(capa.crs(), crs, proyecto)
        extension.combineExtentWith(tr.transformBoundingBox(capa.extent()))
    extension.scale(1.06)

    ms = QgsMapSettings()
    ms.setDestinationCrs(crs)
    ms.setTransformContext(proyecto.transformContext())
    ms.setLayers([c for c in [contorno] + list(capas) + list(fondo) if c is not None])
    ms.setOutputSize(QSize(ancho, alto))
    ms.setOutputDpi(150)
    ms.setExtent(extension)
    ms.setBackgroundColor(QColor("#ffffff"))
    job = QgsMapRendererParallelJob(ms)
    job.start()
    job.waitForFinished()
    imagen = job.renderedImage().convertToFormat(QImage.Format.Format_RGB888)
    _decorar(imagen, ms, crs.isGeographic())
    return imagen


def _renderizar_mapa_lote(capa_activa, todas_capas, capas_extension, fondo, contorno, ancho, alto):
    """Renderiza el mapa de una ficha de lote con zoom al lote activo y máscara sobre vecinos."""
    proyecto = QgsProject.instance()
    crs = proyecto.crs() if proyecto.crs().isValid() else capas_extension[0].crs()

    tr_activo = QgsCoordinateTransform(capa_activa.crs(), crs, proyecto)
    extension_zoom = tr_activo.transformBoundingBox(capa_activa.extent())
    extension_zoom.scale(1.15)

    def _render(capas_render, incluir_fondo, bg_color, extension):
        ms = QgsMapSettings()
        ms.setDestinationCrs(crs)
        ms.setTransformContext(proyecto.transformContext())
        capas_fondo_render = list(fondo) if incluir_fondo else []
        ms.setLayers([c for c in [contorno] + list(capas_render) + capas_fondo_render if c is not None])
        ms.setOutputSize(QSize(ancho, alto))
        ms.setOutputDpi(150)
        ms.setExtent(extension)
        ms.setBackgroundColor(bg_color)
        job = QgsMapRendererParallelJob(ms)
        job.start()
        job.waitForFinished()
        return ms, job.renderedImage().convertToFormat(QImage.Format.Format_ARGB32)

    ms_base, img_base = _render(todas_capas, incluir_fondo=True,
                                bg_color=QColor("#ffffff"), extension=extension_zoom)
    _, img_activo = _render([capa_activa], incluir_fondo=False,
                            bg_color=QColor(0, 0, 0, 0), extension=extension_zoom)

    imagen_final = QImage(ancho, alto, QImage.Format.Format_RGB888)
    imagen_final.fill(QColor("#ffffff"))

    p = QPainter(imagen_final)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(0, 0, img_base)
    p.fillRect(0, 0, ancho, alto, QColor(0, 0, 0, 70))
    p.drawImage(0, 0, img_activo)
    p.end()

    _decorar(imagen_final, ms_base, crs.isGeographic())
    return imagen_final


def _decorar(img, ms, geografico):
    """Marco, flecha de norte y barra de escala."""
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    w, h = img.width(), img.height()
    u = max(w, h) / 100.0

    # Flecha de Norte
    cx, cy = w - 4 * u, 4.5 * u
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 215))
    p.drawEllipse(QPointF(cx, cy + 0.6 * u), 3.2 * u, 3.6 * u)
    flecha = QPolygonF([QPointF(cx, cy - 2.2 * u), QPointF(cx + 1.2 * u, cy + 1.3 * u),
                        QPointF(cx, cy + 0.6 * u), QPointF(cx - 1.2 * u, cy + 1.3 * u)])
    p.setBrush(QColor(COLOR_OSCURO))
    p.drawPolygon(flecha)
    fuente = QFont("Arial")
    fuente.setPixelSize(max(8, int(1.7 * u)))
    fuente.setBold(True)
    p.setFont(fuente)
    p.setPen(QColor(COLOR_OSCURO))
    p.drawText(QRectF(cx - 3 * u, cy + 1.4 * u, 6 * u, 2.4 * u), Qt.AlignmentFlag.AlignCenter, "N")

    # Barra de Escala
    if not geografico:
        m_px = ms.mapUnitsPerPixel()
        objetivo = w * 0.22 * m_px
        potencia = 10 ** math.floor(math.log10(objetivo))
        largo = max(n * potencia for n in (1, 2, 5) if n * potencia <= objetivo)
        px = largo / m_px
        texto = "{:g} km".format(largo / 1000.0) if largo >= 1000 else "{:g} m".format(largo)
        x0, y0 = 2.2 * u, h - 2.4 * u
        fuente.setBold(False)
        fuente.setPixelSize(max(8, int(1.5 * u)))
        p.setFont(fuente)
        ancho_texto = p.fontMetrics().horizontalAdvance(texto)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 225))
        p.drawRect(QRectF(x0 - u, y0 - 2.2 * u, px + ancho_texto + 2.8 * u, 3.2 * u))
        p.setPen(QPen(QColor("#222222"), max(1.0, 0.12 * u)))
        for i in range(4):
            p.setBrush(QColor("#222222") if i % 2 == 0 else QColor("#ffffff"))
            p.drawRect(QRectF(x0 + i * px / 4, y0 - 1.2 * u, px / 4, 0.7 * u))
        p.drawText(QPointF(x0 + px + 0.8 * u, y0 - 0.45 * u), texto)

    p.setPen(QPen(QColor("#555555"), max(1.0, 0.15 * u)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(0, 0, w - 1, h - 1))
    p.end()


# ---------------------------------------------------------------- HTML
def _esc(texto):
    return html.escape(str(texto))


def _leyenda():
    celdas = "".join(
        '<td width="14" bgcolor="{}">&nbsp;</td><td>&nbsp;{}&nbsp;&nbsp;&nbsp;&nbsp;</td>'.format(c, _esc(e))
        for _, e, c in CLASES)
    return '<table cellspacing="0" cellpadding="2" align="center"><tr>{}</tr></table>'.format(celdas)


def _barra(fila):
    celdas = ""
    for i, (valor, _, color) in enumerate(CLASES):
        pct = fila[3 + 2 * i]
        if pct >= 0.5:
            texto = "{} %".format(fmt(pct, 1)) if pct >= 7 else ""
            celdas += ('<td width="{:.1f}%" bgcolor="{}" align="center" style="color:{}; font-size:8pt; '
                       'font-weight:bold">{}</td>').format(pct, color, COLOR_TEXTO[valor], texto)
    return '<table width="100%" cellspacing="0" cellpadding="3"><tr>{}</tr></table>'.format(celdas)


def _kpis(fila):
    cab = ('<td width="25%" bgcolor="{}" align="center" style="color:#ffffff; font-weight:bold; font-size:9pt">'
           'SUPERFICIE TOTAL</td>').format(COLOR_OSCURO)
    val = ('<td bgcolor="#eef3ea" align="center"><span style="font-size:17pt; font-weight:bold; color:{}">{}</span>'
           '<br/><span style="font-size:9pt">hectáreas</span></td>').format(COLOR_OSCURO, fmt(fila[1]))
    for i, (valor, etiqueta, color) in enumerate(CLASES):
        cab += ('<td width="25%" bgcolor="{}" align="center" style="color:{}; font-weight:bold; font-size:9pt">'
                '{}</td>').format(color, COLOR_TEXTO[valor], _esc(etiqueta.upper()))
        val += ('<td bgcolor="{}" align="center"><span style="font-size:17pt; font-weight:bold">{} ha</span>'
                '<br/><span style="font-size:10pt">{} % del área</span></td>').format(
                    TINTE[valor], fmt(fila[2 + 2 * i]), fmt(fila[3 + 2 * i], 1))
    return ('<table width="100%" cellspacing="4" cellpadding="6"><tr>{}</tr><tr>{}</tr></table>'
            .format(cab, val))


def _tabla_lotes(filas, hay_total):
    cab = ('<tr><th align="left">Lote / Zona</th><th align="right">Área total<br/>(ha)</th>'
           + "".join('<th align="right" bgcolor="{c}" style="color:{t}">{e}<br/>(ha)</th>'
                     '<th align="right" bgcolor="{c}" style="color:{t}">%</th>'
                     .format(c=c, t=COLOR_TEXTO[v], e=_esc(e)) for v, e, c in CLASES)
           + '</tr>')
    cuerpo = ""
    for n, fila in enumerate(filas):
        es_total = hay_total and n == len(filas) - 1
        fondo = ' bgcolor="#e4ecdc"' if es_total else (' bgcolor="#f6f8f4"' if n % 2 else "")
        peso = "font-weight:bold; " if es_total else ""
        celdas = '<td style="{}">{}</td>'.format(peso, _esc(fila[0] if es_total else nombre_corto(fila[0])))
        for j, v in enumerate(fila[1:], start=1):
            dec = 1 if j in (2, 4, 6) else 2
            celdas += '<td align="right" style="{}">{}</td>'.format(peso, fmt(v, dec))
        cuerpo += "<tr{}>{}</tr>".format(fondo, celdas)
    return ('<table width="100%" cellspacing="0" cellpadding="4" border="0.5" '
            'style="border-collapse:collapse; border-color:#c8c8c8">{}{}</table>'.format(cab, cuerpo))


def _tabla_datos(pares):
    filas = "".join(
        '<tr><td width="32%" bgcolor="#eef3ea" style="font-weight:bold; color:{}">{}</td><td>{}</td></tr>'
        .format(COLOR_OSCURO, _esc(k), _esc(v)) for k, v in pares if v)
    return ('<table width="100%" cellspacing="0" cellpadding="5" border="0.5" '
            'style="border-collapse:collapse; border-color:#c8c8c8">{}</table>'.format(filas))


def _texto_resumen(filas, resultados):
    t = filas[-1]
    n = len(resultados)
    texto = ("Se {} <b>{} mapa{} de ambiente</b> con una superficie total de <b>{} ha</b>. "
             "La distribución espacial presenta <b>{} ha ({} %)</b> en <b>Zona Alta</b> (loma), "
             "<b>{} ha ({} %)</b> en <b>Zona Media</b> (media loma / transición) y "
             "<b>{} ha ({} %)</b> en <b>Zona Baja</b> (bajos y vías de escurrimiento).").format(
        "analizaron" if n > 1 else "analizó", n, "s" if n > 1 else "", fmt(t[1]),
        fmt(t[6]), fmt(t[7], 1), fmt(t[4]), fmt(t[5], 1), fmt(t[2]), fmt(t[3], 1))
    if n > 1:
        lotes = filas[:-1]
        peor = max(lotes, key=lambda f: f[3])  # mayor proporción de baja
        mejor = max(lotes, key=lambda f: f[7])  # mayor proporción de alta
        texto += (" El lote con mayor proporción de Zona Baja es <b>{}</b> ({} %), "
                  "y el de mayor proporción de Zona Alta es <b>{}</b> ({} %).").format(
            _esc(nombre_corto(peor[0])), fmt(peor[3], 1), _esc(nombre_corto(mejor[0])), fmt(mejor[7], 1))
    return "<p>{}</p>".format(texto)


def _guia(parametros, rangos, filas):
    filas_clase = ""
    descripcion = {
        BAJA: ("Sectores de menor cota topográfica, vías de escurrimiento natural, bajos y cubetas.",
               "Zonas propensas a anegamiento temporario o acumulación de sales. Manejo: monitorear drenaje, "
               "ajustar densidad de siembra y seleccionar cultivares tolerantes a exceso hídrico."),
        MEDIA: ("Zonas de pendiente suave a moderada, media loma y sectores de transición ambiental.",
                "Suelos con balance hídrico intermedio y buen drenaje. Manejo: planteo productivo promedio "
                "con fertilización equilibrada según potencial de la campaña."),
        ALTA: ("Lomas, mesetas y sectores de mayor altitud relativa dentro del lote.",
               "Suelos bien drenados sin riesgo de anegamiento; en campañas secas pueden presentar mayor déficit hídrico. "
               "Manejo: aprovechar el potencial en años húmedos; siembra variable y nutrición de precisión."),
    }
    for valor, etiqueta, color in CLASES:
        filas_clase += (
            '<tr><td width="4%" bgcolor="{c}">&nbsp;</td><td width="16%"><b>{e}</b></td>'
            '<td width="20%">{r}</td><td width="34%">{d}</td><td width="26%">{a}</td></tr>').format(
            c=color, e=_esc(etiqueta), r=_esc(rangos[valor]), d=_esc(descripcion[valor][0]),
            a=_esc(descripcion[valor][1]))

    tabla_clases = (
        '<table width="100%" cellspacing="0" cellpadding="5" border="0.5" '
        'style="border-collapse:collapse; border-color:#c8c8c8">'
        '<tr><th></th><th align="left">Ambiente</th><th align="left">Criterio / Valor</th>'
        '<th align="left">Posición en el relieve</th><th align="left">Recomendación agronómica</th></tr>{}</table>'
        .format(filas_clase))

    partes = [
        '<h2 style="page-break-before:always">4. Cómo interpretar este informe de ambientes</h2>',
        "<h3>4.1 ¿Qué es la zonificación de ambientes?</h3>",
        "<p>La <b>zonificación de ambientes</b> clasifica el relieve y la aptitud productiva del terreno en unidades "
        "homogéneas de manejo. Permite identificar de forma objetiva las lomas (zonas altas), medias lomas y bajos, "
        "facilitando la aplicación variable de insumos (semilla, fertilizantes) y la gestión del agua.</p>",
        "<h3>4.2 Significado de cada ambiente</h3>",
        tabla_clases,
        ESPACIO,
        "<h3>4.3 Aplicaciones prácticas en el campo</h3>",
        "<ul>"
        "<li><b>Siembra variable:</b> ajustar la densidad de plantas según la retención de humedad de cada zona "
        "(generalmente menor densidad en lomas arenosas o con riesgo de secado rápido, y densidades ajustadas en bajos no anegables).</li>"
        "<li><b>Fertilización diferencial:</b> dosificar nitrógeno y fósforo en función de la capacidad de respuesta y "
        "el riesgo hídrico de cada cota topográfica.</li>"
        "<li><b>Sistematización y drenaje:</b> delimitar con precisión las áreas bajas para diseño de canales de desagüe, "
        "terrazas o bordos de contención de escorrentía.</li>"
        "<li><b>Monitoreo diferenciado:</b> evaluar plagas, malezas y enfermedades por separado en zonas bajas y altas.</li>"
        "</ul>",
        "<h3>4.4 Consideraciones técnicas</h3>",
        "<ul>"
        "<li>Las superficies se calculan píxel a píxel a partir de la resolución nativa de cada raster.</li>"
        "<li>Si se utilizó una capa de polígonos (lotes), el cálculo queda estrictamente circunscrito a los límites catastrales definidos.</li>"
        "</ul>",
    ]
    return "".join(partes)


def _html_principal(resultados, filas, parametros, rangos, datos, hay_total, ancho96):
    t = filas[-1]
    datos_portada = [
        ("Proyecto", datos.get("proyecto")),
        ("Cliente / Propiedad", datos.get("cliente")),
        ("Fecha de relevamiento", datos.get("fecha_vuelo")),
        ("Fecha de emisión", parametros["fecha"]),
        ("Elaborado por", datos.get("elaborado")),
        ("Lotes / Sectores analizados", str(len(resultados))),
        ("Superficie total analizada", "{} ha".format(fmt(t[1]))),
    ]
    portada = [
        "<h1>Informe de Zonificación de Ambientes</h1>",
        '<p class="sub">Delimitación topográfica y productiva — Superficie de Zona Baja, Media y Alta</p>',
        "<p>&nbsp;</p>",
        _tabla_datos(datos_portada),
        ESPACIO,
        "<h2>1. Resumen general</h2>",
        _kpis(t),
        ESPACIO,
        _barra(t),
        ESPACIO,
        _texto_resumen(filas, resultados),
    ]
    mapa = [
        '<h2 style="page-break-before:always">2. Mapa general de ambientes</h2>',
        '<p align="center"><img src="img://mapa_general" width="{}"/></p>'.format(ancho96),
        _leyenda(),
        '<p class="nota" align="center">Zonificación de ambientes. Rojo: Zona Baja · Amarillo: Zona Media · Verde: Zona Alta.</p>',
    ]
    tabla = [
        '<h2 style="page-break-before:always">3. Superficie por lote / sector</h2>',
        "<p>Superficie en hectáreas y porcentaje de cada ambiente respecto del área total de cada lote.</p>",
        _tabla_lotes(filas, hay_total),
        '<p class="nota">Criterios: {}.</p>'.format(" · ".join(
            "{}: {}".format(_esc(NOMBRE[v]), _esc(rangos[v])) for v, _, _ in CLASES)),
    ]
    return ("<html><head><style>{}</style></head><body>{}{}{}{}</body></html>".format(
        CSS, "".join(portada), "".join(mapa), "".join(tabla), _guia(parametros, rangos, filas)))


def _html_ficha(numero, fila, rangos, res, ancho96):
    nombre = nombre_corto(fila[0])
    titulo_seccion = "<h2>5. Fichas por lote</h2>" if numero == 1 else ""
    filas_clase = ""
    for i, (valor, etiqueta, color) in enumerate(CLASES):
        filas_clase += (
            '<tr><td bgcolor="{c}" width="4%">&nbsp;</td><td>{e}</td><td>{r}</td>'
            '<td align="right">{ha}</td><td align="right">{p} %</td></tr>').format(
            c=color, e=_esc(etiqueta), r=_esc(rangos[valor]), ha=fmt(fila[2 + 2 * i]), p=fmt(fila[3 + 2 * i], 1))

    tabla = (
        '<table width="100%" cellspacing="0" cellpadding="4" border="0.5" '
        'style="border-collapse:collapse; border-color:#c8c8c8">'
        '<tr><th></th><th align="left">Ambiente</th><th align="left">Criterio</th>'
        '<th align="right">Superficie (ha)</th><th align="right">% del lote</th></tr>{}'
        '<tr bgcolor="#e4ecdc"><td></td><td colspan="2"><b>Área total del lote</b></td>'
        '<td align="right"><b>{}</b></td><td align="right"><b>100 %</b></td></tr></table>').format(
        filas_clase, fmt(fila[1]))

    texto = ("<p>El lote <b>{}</b> cuenta con una superficie total de <b>{} ha</b>: "
             "el <b>{} %</b> (<b>{} ha</b>) corresponde a <b>Zona Alta</b>, "
             "el <b>{} %</b> (<b>{} ha</b>) a <b>Zona Media</b> y "
             "el <b>{} %</b> (<b>{} ha</b>) a <b>Zona Baja</b>.</p>").format(
        _esc(nombre), fmt(fila[1]),
        fmt(fila[7], 1), fmt(fila[6]),
        fmt(fila[5], 1), fmt(fila[4]),
        fmt(fila[3], 1), fmt(fila[2]))

    return ("<html><head><style>{css}</style></head><body>{seccion}"
            "<h3 style=\"font-size:13pt\">5.{n} Ficha de lote: {nombre}</h3>"
            '<p align="center"><img src="img://mapa_lote" width="{w}"/></p>{ley}'
            "{tabla}<p>&nbsp;</p>{barra}{texto}"
            '<p class="nota">Archivo: {arch} · Resolución de píxel: {px} m</p>'
            "</body></html>").format(
        css=CSS, seccion=titulo_seccion, n=numero, nombre=_esc(nombre), w=ancho96, ley=_leyenda(), tabla=tabla, barra=_barra(fila),
        texto=texto, arch=_esc(os.path.basename(res["ruta"])), px=fmt(res["resolucion"][0], 2))


def _html_anexo(resultados, parametros, rangos, numero):
    crs = QgsProject.instance().crs()
    filas = "".join(
        '<tr><td>{}</td><td>{}</td><td align="right">{} × {}</td><td align="right">{}</td></tr>'.format(
            _esc(nombre_corto(r["nombre"])), _esc(os.path.basename(r["ruta"])),
            fmt(r["resolucion"][0], 2), fmt(r["resolucion"][1], 2), fmt(r["pixeles"], 0))
        for r in resultados)
    criterios = "".join("<li><b>{}:</b> {}</li>".format(_esc(NOMBRE[v]), _esc(rangos[v])) for v, _, _ in CLASES)
    metodo = {
        "clases": "Clasificación directa a partir de valores de clase enteros en el raster (1 = Baja, 2 = Media, 3 = Alta).",
        "rgb": "Clasificación por tono de color HSV sobre imagen RGB (rojo, amarillo, verde).",
        "continuo": "Clasificación por umbrales numéricos sobre modelo continuo de elevación / cota.",
    }.get(parametros.get("modo"), "Clasificación píxel a píxel.")

    return ("<html><head><style>{css}</style></head><body>"
            "<h2>{n}. Anexo técnico: metodología y datos de origen</h2>"
            "<p><b>Origen de los datos:</b> {origen}.</p>"
            "<p><b>Método:</b> {metodo} El cálculo de superficie se realiza acumulando el área real de cada píxel, "
            "compensada por proyección cartográfica{crs}.</p>"
            "<p><b>Criterios aplicados:</b></p><ul>{criterios}</ul>"
            "{limite}" + ESPACIO +
            '<table width="100%" cellspacing="0" cellpadding="4" border="0.5" '
            'style="border-collapse:collapse; border-color:#c8c8c8">'
            '<tr><th align="left">Lote</th><th align="left">Archivo</th>'
            '<th align="right">Píxel (m)</th><th align="right">Píxeles válidos</th></tr>{filas}</table>'
            '<p class="nota">Informe generado con el complemento Mapa de Ambientes para QGIS · LaSierra 360 · {fecha}.</p>'
            "</body></html>").format(
        css=CSS, n=numero, origen=_esc(parametros["origen"]), metodo=metodo,
        crs=" (sistema de referencia {})".format(_esc(crs.authid())) if crs.isValid() else "",
        criterios=criterios,
        limite=("<p><b>Límite por polígono:</b> {}.</p>".format(_esc(parametros["poligono"]))
                if parametros.get("poligono") else ""),
        filas=filas, fecha=_esc(parametros["fecha"]))


# ---------------------------------------------------------------- PDF
CABECERA_MM = 17
PIE_MM = 10
MAPA_GENERAL = (2200, 1500)
MAPA_LOTE = (2000, 1300)


def _documento(writer, html_texto, recursos=None):
    doc = QTextDocument()
    doc.documentLayout().setPaintDevice(writer)
    doc.setDefaultFont(QFont("Arial", 10))
    doc.setDocumentMargin(0)
    for nombre, imagen in (recursos or {}).items():
        doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl(nombre), imagen)
    doc.setHtml(html_texto)
    doc.setPageSize(QSizeF(writer.width(), writer.height() - _mm(CABECERA_MM) - _mm(PIE_MM)))
    return doc


class _Paginador:
    def __init__(self, writer, datos, total_paginas):
        self.writer = writer
        self.datos = datos
        self.total = total_paginas
        self.pagina = 0
        self.ancho = writer.width()
        self.alto = writer.height()
        self.cab = _mm(CABECERA_MM)
        self.pie = _mm(PIE_MM)
        self.cuerpo = self.alto - self.cab - self.pie
        self.logo = QImage(LOGO) if os.path.exists(LOGO) else QImage()
        self.painter = QPainter(writer)
        self.painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    def dibujar(self, doc):
        p = self.painter
        for i in range(doc.pageCount()):
            if self.pagina > 0:
                self.writer.newPage()
            self.pagina += 1
            p.fillRect(QRectF(0, 0, self.ancho, self.alto), QColor("#ffffff"))
            self._encabezado()
            p.save()
            p.translate(0, self.cab - i * self.cuerpo)
            doc.drawContents(p, QRectF(0, i * self.cuerpo, self.ancho, self.cuerpo))
            p.restore()
            self._pie()

    def _encabezado(self):
        p = self.painter
        alto_logo = _mm(12)
        if not self.logo.isNull():
            ancho_logo = alto_logo * self.logo.width() / self.logo.height()
            p.drawImage(QRectF(0, 0, ancho_logo, alto_logo), self.logo)
        fuente = QFont("Arial", 11)
        fuente.setBold(True)
        p.setFont(fuente)
        p.setPen(QColor(COLOR_OSCURO))
        p.drawText(QRectF(0, 0, self.ancho, _mm(6)), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "Informe de Zonificación de Ambientes")
        fuente = QFont("Arial")
        fuente.setPointSizeF(8.5)
        p.setFont(fuente)
        p.setPen(QColor("#555555"))
        sub = " · ".join(x for x in (self.datos.get("proyecto"), self.datos.get("cliente")) if x)
        p.drawText(QRectF(0, _mm(6), self.ancho, _mm(5)), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   sub)
        p.setPen(QPen(QColor(COLOR_MARCA), _mm(0.6)))
        p.drawLine(QPointF(0, _mm(13.5)), QPointF(self.ancho, _mm(13.5)))

    def _pie(self):
        p = self.painter
        y = self.alto - self.pie + _mm(3)
        p.setPen(QPen(QColor("#bbbbbb"), _mm(0.25)))
        p.drawLine(QPointF(0, y), QPointF(self.ancho, y))
        p.setFont(QFont("Arial", 8))
        p.setPen(QColor("#555555"))
        izquierda = "LaSierra 360 · Informe de Ambientes Topográficos"
        if self.datos.get("fecha_vuelo"):
            izquierda += " · Relevamiento: {}".format(self.datos["fecha_vuelo"])
        caja = QRectF(0, y + _mm(1), self.ancho, _mm(5))
        p.drawText(caja, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, izquierda)
        p.drawText(caja, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "Página {} de {}".format(self.pagina, self.total))

    def terminar(self):
        self.painter.end()


def generar_pdf(ruta, resultados, parametros, rangos, datos, capas_fondo=(), capa_limite=None,
                fichas=True, progreso=None):
    """Genera el informe formal de ambientes en `ruta`."""
    def avisar(texto):
        if progreso is not None:
            progreso(texto)

    filas = [fila_informe(r["nombre"], r["areas_m2"]) for r in resultados]
    hay_total = len(resultados) > 1
    if hay_total:
        filas.append(fila_informe("TOTAL", {c: sum(r["areas_m2"][c] for r in resultados)
                                            for c in (BAJA, MEDIA, ALTA)}))

    writer = QPdfWriter(ruta)
    writer.setResolution(DPI)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(15, 12, 15, 12), QPageLayout.Unit.Millimeter)
    writer.setTitle("Informe de Zonificación de Ambientes")
    writer.setCreator("Mapa de Ambientes – LaSierra 360")

    ancho = writer.width()
    ancho96 = int(ancho * 96 / DPI)

    capas = [capa_para_mapa(r) for r in resultados]
    validas = [c for c in capas if c is not None]
    contorno = capa_contorno(capa_limite)

    avisar("Generando mapa general de ambientes…")
    recursos = {}
    if validas:
        recursos["img://mapa_general"] = renderizar_mapa(validas, validas, capas_fondo, contorno, *MAPA_GENERAL)

    principal = _documento(writer, _html_principal(resultados, filas, parametros, rangos, datos,
                                                   hay_total, ancho96), recursos)
    anexo = _documento(writer, _html_anexo(resultados, parametros, rangos, 6 if fichas else 5))
    total = principal.pageCount() + anexo.pageCount()

    if fichas:
        blanco = QImage(MAPA_LOTE[0], MAPA_LOTE[1], QImage.Format.Format_RGB888)
        blanco.fill(QColor("#ffffff"))
        for i, res in enumerate(resultados, start=1):
            total += _documento(writer, _html_ficha(i, filas[i - 1], rangos, res, ancho96),
                                {"img://mapa_lote": blanco}).pageCount()

    pag = _Paginador(writer, datos, total)
    try:
        pag.dibujar(principal)
        del principal, recursos

        if fichas:
            for i, (res, capa) in enumerate(zip(resultados, capas), start=1):
                avisar("Generando ficha {}/{}: {}…".format(i, len(resultados), nombre_corto(res["nombre"])))
                img = {}
                if capa is not None:
                    if len(validas) > 1:
                        img["img://mapa_lote"] = _renderizar_mapa_lote(
                            capa, validas, validas, capas_fondo, contorno, *MAPA_LOTE)
                    else:
                        img["img://mapa_lote"] = renderizar_mapa(
                            validas, [capa], capas_fondo, contorno, *MAPA_LOTE)
                pag.dibujar(_documento(writer, _html_ficha(i, filas[i - 1], rangos, res, ancho96), img))

        pag.dibujar(anexo)
    finally:
        pag.terminar()
    return pag.pagina

