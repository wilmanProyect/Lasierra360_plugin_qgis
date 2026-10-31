# -*- coding: utf-8 -*-
"""Informe PDF formal: logo, mapas, tabla de superficies y guía de lectura.

El documento se arma con QTextDocument (HTML) y se pagina a mano sobre un
QPdfWriter para dibujar encabezado y pie en cada hoja. Cada ficha de lote es un
documento aparte, así sólo un mapa por vez queda en memoria.
"""
import html
import math
import os
import re

from qgis.core import (
    QgsCoordinateTransform,
    QgsFillSymbol,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsPalettedRasterRenderer,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
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

from .analisis import ALTA, BAJA, CLASES, SUELO
from .formato import fila_informe, fmt

COLOR_OSCURO = "#0e3511"
COLOR_MARCA = "#7dc300"
TINTE = {SUELO: "#fbe1e1", BAJA: "#fff5c2", ALTA: "#dff1e3"}
COLOR = {valor: color for valor, _, color in CLASES}
NOMBRE = {valor: etiqueta for valor, etiqueta, _ in CLASES}
COLOR_TEXTO = {SUELO: "#ffffff", BAJA: "#3a3000", ALTA: "#ffffff"}

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


# QTextDocument ignora el margin-top de un título que sigue a una tabla: se separa con un párrafo vacío
ESPACIO = '<p style="font-size:6pt">&nbsp;</p>'


def _mm(mm):
    return mm * DPI / 25.4


def nombre_corto(nombre):
    """'NDVI LOTE A13.rgb' -> 'LOTE A13'"""
    nombre = re.sub(r"\.rgb$", "", nombre.strip(), flags=re.IGNORECASE)
    return re.sub(r"^NDVI\s+", "", nombre, flags=re.IGNORECASE)


# ---------------------------------------------------------------- mapas
def capa_para_mapa(res):
    """Raster clasificado (rojo/amarillo/verde) si existe; si no, el ortomosaico original."""
    ruta = res.get("ruta_clasificado")
    if ruta and os.path.exists(ruta):
        capa = QgsRasterLayer(ruta, res["nombre"])
        if capa.isValid():
            clases = [QgsPalettedRasterRenderer.Class(v, QColor(c), e) for v, e, c in CLASES]
            capa.setRenderer(QgsPalettedRasterRenderer(capa.dataProvider(), 1, clases))
            return capa
    capa = QgsRasterLayer(res["ruta"], res["nombre"])
    return capa if capa.isValid() else None


def capa_contorno(capa_vec):
    """Copia de la capa de polígonos dibujada sólo con contorno."""
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
    """Renderiza el mapa de una ficha de lote con zoom al lote activo:
    - El encuadre final hace zoom al lote activo (+ 15 % de margen).
    - Fondo: todos los lotes + satélite con máscara oscura.
    - El lote activo se dibuja encima con fondo transparente y colores NDVI completos.
    """
    proyecto = QgsProject.instance()
    crs = proyecto.crs() if proyecto.crs().isValid() else capas_extension[0].crs()

    # ── Extensión AMPLIA: todos los lotes (para renderizar el fondo completo con contexto)
    extension_todos = QgsRectangle()
    extension_todos.setMinimal()
    for capa in capas_extension:
        tr = QgsCoordinateTransform(capa.crs(), crs, proyecto)
        extension_todos.combineExtentWith(tr.transformBoundingBox(capa.extent()))
    extension_todos.scale(1.06)

    # ── Extensión ZOOM: solo el lote activo con margen generoso (15 %) → el lote llena el frame
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

    # Paso 1: Fondo completo (todos los lotes + satélite) usando extensión ZOOM
    #         → el frame ya muestra solo el área del lote activo
    ms_base, img_base = _render(todas_capas, incluir_fondo=True,
                                bg_color=QColor("#ffffff"), extension=extension_zoom)

    # Paso 2: Solo el lote activo, sin satélite, fondo transparente, misma extensión ZOOM
    #         → solo los píxeles reales del lote son opacos
    _, img_activo = _render([capa_activa], incluir_fondo=False,
                            bg_color=QColor(0, 0, 0, 0), extension=extension_zoom)

    # Paso 3: Composición final
    imagen_final = QImage(ancho, alto, QImage.Format.Format_RGB888)
    imagen_final.fill(QColor("#ffffff"))

    p = QPainter(imagen_final)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # 3a. Fondo con todos los lotes (satélite + NDVI vecinos) — encuadrado en el lote activo
    p.drawImage(0, 0, img_base)

    # 3b. Máscara oscura semitransparente sobre toda la imagen
    p.fillRect(0, 0, ancho, alto, QColor(0, 0, 0, 160))

    # 3c. Lote activo encima: píxeles transparentes = máscara visible; opacos = NDVI completo
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

    # norte
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

    # escala
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
           'ÁREA TOTAL ANALIZADA</td>').format(COLOR_OSCURO)
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


def _kpis_rinde(fila, parametros):
    if not parametros.get("habilitado") or len(fila) < 12:
        return ""
    cultivo = _esc(parametros.get("cultivo", "Cultivo"))
    estadio_cod = _esc(parametros.get("estadio_codigo", ""))
    estadio_nom = _esc(parametros.get("estadio_nombre", "").split("–")[-1].strip())
    cab = (
        '<td width="33%" bgcolor="{o}" align="center" style="color:#ffffff; font-weight:bold; font-size:9pt">'
        'PRODUCCIÓN TOTAL ESTIMADA</td>'
        '<td width="33%" bgcolor="{o}" align="center" style="color:#ffffff; font-weight:bold; font-size:9pt">'
        'RENDIMIENTO PROMEDIO PONDERADO</td>'
        '<td width="34%" bgcolor="{o}" align="center" style="color:#ffffff; font-weight:bold; font-size:9pt">'
        'CULTIVO Y ESTADIO FENOLÓGICO</td>'
    ).format(o="#2d5016")
    val = (
        '<td bgcolor="#eef5e8" align="center"><span style="font-size:16pt; font-weight:bold; color:#2d5016">{} t</span>'
        '<br/><span style="font-size:8.5pt">producción estimada</span></td>'
        '<td bgcolor="#eef5e8" align="center"><span style="font-size:16pt; font-weight:bold; color:#2d5016">{} t/ha</span>'
        '<br/><span style="font-size:8.5pt">media ponderada</span></td>'
        '<td bgcolor="#eef5e8" align="center"><span style="font-size:14pt; font-weight:bold; color:#2d5016">{} ({})</span>'
        '<br/><span style="font-size:8.5pt">{}</span></td>'
    ).format(fmt(fila[11], 1), fmt(fila[10], 2), cultivo, estadio_cod, estadio_nom)
    return '<table width="100%" cellspacing="4" cellpadding="6"><tr>{}</tr><tr>{}</tr></table>'.format(cab, val)


def _tabla_lotes(filas, hay_total, parametros=None):
    rinde_hab = bool(parametros and parametros.get("habilitado"))
    col_extra_cab = ""
    if rinde_hab:
        col_extra_cab = (
            '<th align="right" bgcolor="#2d5016" style="color:#ffffff">Rinde est.<br/>(t/ha)</th>'
            '<th align="right" bgcolor="#2d5016" style="color:#ffffff">Producción<br/>(t)</th>'
        )
    cab = ('<tr><th align="left">Lote</th><th align="right">Área total<br/>(ha)</th>'
           + "".join('<th align="right" bgcolor="{c}" style="color:{t}">{e}<br/>(ha)</th>'
                     '<th align="right" bgcolor="{c}" style="color:{t}">%</th>'
                     .format(c=c, t=COLOR_TEXTO[v], e=_esc(e)) for v, e, c in CLASES)
           + '<th align="right">Vegetación<br/>total (ha)</th><th align="right">%</th>'
           + col_extra_cab + '</tr>')
    cuerpo = ""
    for n, fila in enumerate(filas):
        es_total = hay_total and n == len(filas) - 1
        fondo = ' bgcolor="#e4ecdc"' if es_total else (' bgcolor="#f6f8f4"' if n % 2 else "")
        peso = "font-weight:bold; " if es_total else ""
        celdas = '<td style="{}">{}</td>'.format(peso, _esc(fila[0] if es_total else nombre_corto(fila[0])))
        for j, v in enumerate(fila[1:10], start=1):
            dec = 1 if j in (3, 5, 7, 9) else 2
            celdas += '<td align="right" style="{}">{}</td>'.format(peso, fmt(v, dec))
        if rinde_hab and len(fila) >= 12:
            celdas += '<td align="right" style="{}background-color:{};">{}</td>'.format(
                peso, "#e8f2e0" if not es_total else "#d5e6cb", fmt(fila[10], 2))
            celdas += '<td align="right" style="{}background-color:{};">{}</td>'.format(
                peso, "#e8f2e0" if not es_total else "#d5e6cb", fmt(fila[11], 1))
        cuerpo += "<tr{}>{}</tr>".format(fondo, celdas)
    return ('<table width="100%" cellspacing="0" cellpadding="4" border="0.5" '
            'style="border-collapse:collapse; border-color:#c8c8c8">{}{}</table>'.format(cab, cuerpo))


def _tabla_datos(pares):
    filas = "".join(
        '<tr><td width="32%" bgcolor="#eef3ea" style="font-weight:bold; color:{}">{}</td><td>{}</td></tr>'
        .format(COLOR_OSCURO, _esc(k), _esc(v)) for k, v in pares if v)
    return ('<table width="100%" cellspacing="0" cellpadding="5" border="0.5" '
            'style="border-collapse:collapse; border-color:#c8c8c8">{}</table>'.format(filas))


def _texto_resumen(filas, resultados, parametros=None):
    t = filas[-1]
    n = len(resultados)
    texto = ("Se {} <b>{} ortomosaico{}</b> con una superficie total de <b>{} ha</b>. "
             "De esa superficie, <b>{} ha ({} %)</b> presentan <b>alta vegetación</b>, "
             "<b>{} ha ({} %)</b> <b>baja vegetación</b> y <b>{} ha ({} %)</b> corresponden a <b>suelo</b> "
             "sin cobertura vegetal. La superficie con vegetación (amarillo + verde) suma "
             "<b>{} ha</b>, es decir el <b>{} %</b> del área analizada.").format(
        "analizaron" if n > 1 else "analizó", n, "s" if n > 1 else "", fmt(t[1]),
        fmt(t[6]), fmt(t[7], 1), fmt(t[4]), fmt(t[5], 1), fmt(t[2]), fmt(t[3], 1), fmt(t[8]), fmt(t[9], 1))
    if n > 1:
        lotes = filas[:-1]
        peor = max(lotes, key=lambda f: f[3])
        mejor = max(lotes, key=lambda f: f[7])
        texto += (" El lote con mayor proporción de suelo es <b>{}</b> ({} %), y el de mayor proporción de "
                  "alta vegetación es <b>{}</b> ({} %).").format(
            _esc(nombre_corto(peor[0])), fmt(peor[3], 1), _esc(nombre_corto(mejor[0])), fmt(mejor[7], 1))
    if parametros and parametros.get("habilitado") and len(t) >= 12:
        texto += ("<br/><br/><b>Estimación de producción:</b> para el cultivo <b>{}</b> en estadio <b>{}</b>, "
                  "se proyecta una producción total de <b>{} t</b> con un rendimiento promedio ponderado de "
                  "<b>{} t/ha</b>.").format(
            _esc(parametros.get("cultivo", "")), _esc(parametros.get("estadio_nombre", "")),
            fmt(t[11], 1), fmt(t[10], 2))
    return "<p>{}</p>".format(texto)


def _guia(parametros, rangos, filas):
    modo_rgb = parametros["modo"] == "rgb"
    ej = filas[0]
    filas_clase = ""
    descripcion = {
        SUELO: ("Suelo desnudo o con muy poca vegetación: zonas sin emergencia, fallas de siembra, "
                "caminos, canales, rastrojo o plantas con estrés severo.",
                "Verificar en campo; evaluar resiembra o causas de la falla."),
        BAJA: ("Vegetación presente pero con poca cobertura o vigor: cultivo en emergencia o etapa "
               "temprana, plantas pequeñas, estrés moderado (agua, nutrientes, plagas) o malezas dispersas.",
               "Monitorear la evolución y revisar posibles causas de estrés."),
        ALTA: ("Cultivo con buena cobertura y vigor; las plantas cubren el suelo y están fotosintéticamente "
               "activas.", "Mantener el manejo; usar como referencia del lote."),
    }
    for valor, etiqueta, color in CLASES:
        filas_clase += (
            '<tr><td width="4%" bgcolor="{c}">&nbsp;</td><td width="16%"><b>{e}</b></td>'
            '<td width="20%">{r}</td><td width="36%">{d}</td><td width="24%">{a}</td></tr>').format(
            c=color, e=_esc(etiqueta), r=_esc(rangos[valor]), d=_esc(descripcion[valor][0]),
            a=_esc(descripcion[valor][1]))
    tabla_clases = (
        '<table width="100%" cellspacing="0" cellpadding="5" border="0.5" '
        'style="border-collapse:collapse; border-color:#c8c8c8">'
        '<tr><th></th><th align="left">Clase</th><th align="left">Criterio</th>'
        '<th align="left">Qué indica</th><th align="left">Acción sugerida</th></tr>{}</table>'.format(filas_clase))

    partes = [
        '<h2 style="page-break-before:always">4. Cómo leer este informe</h2>',
        "<h3>4.1 ¿Qué es el NDVI?</h3>",
        "<p>El <b>NDVI</b> (Índice de Vegetación de Diferencia Normalizada) mide el vigor de la vegetación a "
        "partir de la luz que reflejan las plantas: una planta sana absorbe la luz roja y refleja mucha luz "
        "infrarroja cercana. Sus valores van de −1 a 1: cerca de <b>1</b> indica vegetación densa y vigorosa; "
        "cerca de <b>0</b> o negativo indica suelo desnudo, agua o material sin vida.</p>",
        "<h3>4.2 Significado de los colores</h3>",
        tabla_clases,
        ESPACIO,
        "<h3>4.3 Cómo leer las cifras</h3>",
        "<ul>"
        "<li><b>Área total analizada (ha):</b> superficie cubierta por la imagen con dato válido{}. "
        "No incluye el fondo transparente fuera del vuelo.</li>"
        "<li><b>Hectáreas por color:</b> suma de la superficie de todos los píxeles de ese color. Cada píxel "
        "representa un cuadrado de terreno de pocos centímetros, por lo que el cálculo es muy detallado.</li>"
        "<li><b>Porcentaje (%):</b> hectáreas del color divididas por el área total del lote, multiplicado "
        "por 100. Los tres colores suman 100 %.</li>"
        "<li><b>Vegetación total:</b> suma de baja + alta vegetación (amarillo + verde). Indica la superficie "
        "donde hay cultivo presente, es decir el <b>porcentaje de siembra efectiva</b>.</li>"
        "</ul>".format(" dentro de los polígonos de lote" if parametros.get("poligono") else ""),
        "<p><b>Ejemplo:</b> en {lote}, de un total de {tot} ha, {verde} ha ({pv} %) tienen alta vegetación, "
        "{am} ha ({pa} %) baja vegetación y {rojo} ha ({pr} %) son suelo. La vegetación total es de {veg} ha "
        "({pveg} %).</p>".format(
            lote=_esc(nombre_corto(ej[0])), tot=fmt(ej[1]), verde=fmt(ej[6]), pv=fmt(ej[7], 1),
            am=fmt(ej[4]), pa=fmt(ej[5], 1), rojo=fmt(ej[2]), pr=fmt(ej[3], 1), veg=fmt(ej[8]),
            pveg=fmt(ej[9], 1)),
        "<h3>4.4 Recomendaciones de uso</h3>",
        "<ul>"
        "<li>Las <b>zonas rojas dentro de un lote sembrado</b> señalan sitios a verificar en campo "
        "(fallas de siembra, encharcamiento, compactación, daño por plagas).</li>"
        "<li>Las <b>zonas amarillas</b> conviene monitorearlas en los siguientes vuelos: si pasan a verde, el "
        "cultivo se está desarrollando; si pasan a rojo, hay un problema.</li>"
        "<li>Compare el mismo lote en distintas fechas para seguir la evolución del cultivo.</li>"
        "</ul>",
        "<h3>4.5 Consideraciones</h3>",
        "<ul>",
    ]
    if modo_rgb:
        partes.append(
            "<li>La clasificación se hizo sobre la imagen NDVI coloreada, píxel por píxel, según su color. "
            "La escala de colores de cada ortomosaico se ajusta a sus propios valores mínimo y máximo, por "
            "lo que un mismo color puede corresponder a valores de NDVI algo distintos entre lotes.</li>")
    if not parametros.get("poligono"):
        partes.append(
            "<li>El área incluye todo lo que cubre la imagen (también caminos, canales o bordes "
            "dentro del vuelo).</li>")
    partes.append(
        "<li>Si dos ortomosaicos se superponen, la zona común se suma en ambos lotes en el total.</li>"
        "<li>Los resultados reflejan el estado del cultivo en la fecha del vuelo.</li></ul>")

    if parametros.get("habilitado"):
        partes.append(
            "<h3>4.6 Estimación de Rendimiento y Estadio Fenológico</h3>"
            "<p>El análisis integra la estimación de productividad para el cultivo <b>{cultivo}</b> en "
            "estadio fenológico <b>{estadio}</b>.</p>"
            "<p><b>Criterio agronómico del estadio:</b> {ayuda}</p>"
            "<p><b>Metodología de cálculo:</b> la producción total de cada lote se obtiene sumando la superficie "
            "de cada clase de vigor multiplicada por su rendimiento de referencia estimado (Alta vegetación: {ra} t/ha, "
            "Baja vegetación: {rb} t/ha, Suelo: {rs} t/ha). El rendimiento promedio del lote y del campo corresponde a "
            "la media ponderada por hectárea.</p>".format(
                cultivo=_esc(parametros.get("cultivo", "")),
                estadio=_esc(parametros.get("estadio_nombre", "")),
                ayuda=_esc(parametros.get("estadio_ayuda", "")),
                ra=fmt(parametros.get("rinde_alta", 0.0), 2),
                rb=fmt(parametros.get("rinde_baja", 0.0), 2),
                rs=fmt(parametros.get("rinde_suelo", 0.0), 2),
            )
        )
    return "".join(partes)


def _html_principal(resultados, filas, parametros, rangos, datos, hay_total, ancho96):
    t = filas[-1]
    rinde_hab = bool(parametros and parametros.get("habilitado") and len(t) >= 12)
    datos_portada = [
        ("Proyecto", datos.get("proyecto")),
        ("Cliente / Propiedad", datos.get("cliente")),
        ("Fecha de vuelo", datos.get("fecha_vuelo")),
        ("Fecha de emisión", parametros["fecha"]),
        ("Elaborado por", datos.get("elaborado")),
        ("Lotes analizados", str(len(resultados))),
        ("Superficie total analizada", "{} ha".format(fmt(t[1]))),
    ]
    if rinde_hab:
        datos_portada.extend([
            ("Cultivo", parametros.get("cultivo")),
            ("Estadio fenológico", parametros.get("estadio_nombre")),
            ("Rendimiento promedio ponderado", "{} t/ha".format(fmt(t[10], 2))),
            ("Producción total estimada", "{} t".format(fmt(t[11], 1))),
        ])
    portada = [
        "<h1>Informe de Cobertura Vegetal y Rendimiento</h1>" if rinde_hab else "<h1>Informe de Cobertura Vegetal</h1>",
        '<p class="sub">Análisis de ortomosaicos NDVI — superficie, vigor vegetal y producción estimada</p>'
        if rinde_hab else
        '<p class="sub">Análisis de ortomosaicos NDVI — superficie de suelo, baja y alta vegetación</p>',
        "<p>&nbsp;</p>",
        _tabla_datos(datos_portada),
        ESPACIO,
        "<h2>1. Resumen general</h2>",
        _kpis(t),
    ]
    if rinde_hab:
        portada.extend([
            ESPACIO,
            _kpis_rinde(t, parametros),
        ])
    portada.extend([
        ESPACIO,
        _barra(t),
        ESPACIO,
        _texto_resumen(filas, resultados, parametros),
    ])
    mapa = [
        '<h2 style="page-break-before:always">2. Mapa general de cobertura</h2>',
        '<p align="center"><img src="img://mapa_general" width="{}"/></p>'.format(ancho96),
        _leyenda(),
        '<p class="nota" align="center">Clasificación píxel a píxel del NDVI. Rojo: suelo · Amarillo: baja '
        'vegetación · Verde: alta vegetación.</p>',
    ]
    tabla = [
        '<h2 style="page-break-before:always">3. Superficie y producción por lote</h2>' if rinde_hab else
        '<h2 style="page-break-before:always">3. Superficie por lote</h2>',
        "<p>Superficie en hectáreas, porcentaje y producción estimada de cada lote.</p>" if rinde_hab else
        "<p>Superficie en hectáreas y porcentaje de cada clase respecto del área total de cada lote.</p>",
        _tabla_lotes(filas, hay_total, parametros),
        '<p class="nota">Criterios: {}.</p>'.format(" · ".join(
            "{}: {}".format(_esc(NOMBRE[v]), _esc(rangos[v])) for v, _, _ in CLASES)),
    ]
    return ("<html><head><style>{}</style></head><body>{}{}{}{}</body></html>".format(
        CSS, "".join(portada), "".join(mapa), "".join(tabla), _guia(parametros, rangos, filas)))


def _html_ficha(numero, fila, rangos, res, ancho96, parametros=None):
    nombre = nombre_corto(fila[0])
    titulo_seccion = "<h2>5. Fichas por lote</h2>" if numero == 1 else ""
    filas_clase = ""
    for i, (valor, etiqueta, color) in enumerate(CLASES):
        filas_clase += (
            '<tr><td bgcolor="{c}" width="4%">&nbsp;</td><td>{e}</td><td>{r}</td>'
            '<td align="right">{ha}</td><td align="right">{p} %</td></tr>').format(
            c=color, e=_esc(etiqueta), r=_esc(rangos[valor]), ha=fmt(fila[2 + 2 * i]), p=fmt(fila[3 + 2 * i], 1))

    filas_extra_tabla = ""
    texto_extra_rinde = ""
    if parametros and parametros.get("habilitado") and len(fila) >= 12:
        filas_extra_tabla = (
            '<tr bgcolor="#eef5e8"><td></td><td colspan="2"><b>Rendimiento promedio estimado</b></td>'
            '<td align="right"><b>{} t/ha</b></td><td align="right">--</td></tr>'
            '<tr bgcolor="#e4ecdc"><td></td><td colspan="2"><b>Producción estimada del lote</b></td>'
            '<td align="right"><b>{} t</b></td><td align="right">--</td></tr>'
        ).format(fmt(fila[10], 2), fmt(fila[11], 1))
        texto_extra_rinde = (" Se proyecta una cosecha de <b>{} t</b> con un rendimiento promedio de "
                             "<b>{} t/ha</b> (Estadio: <b>{}</b>).").format(
            fmt(fila[11], 1), fmt(fila[10], 2), _esc(parametros.get("estadio_codigo", "")))

    tabla = (
        '<table width="100%" cellspacing="0" cellpadding="4" border="0.5" '
        'style="border-collapse:collapse; border-color:#c8c8c8">'
        '<tr><th></th><th align="left">Clase</th><th align="left">Criterio</th>'
        '<th align="right">Superficie (ha)</th><th align="right">% del lote</th></tr>{}'
        '<tr bgcolor="#eef3ea"><td></td><td colspan="2"><b>Vegetación total (baja + alta)</b></td>'
        '<td align="right"><b>{}</b></td><td align="right"><b>{} %</b></td></tr>'
        '<tr bgcolor="#e4ecdc"><td></td><td colspan="2"><b>Área total del lote</b></td>'
        '<td align="right"><b>{}</b></td><td align="right"><b>100 %</b></td></tr>{}</table>').format(
        filas_clase, fmt(fila[8]), fmt(fila[9], 1), fmt(fila[1]), filas_extra_tabla)
    texto = ("<p>Del total de <b>{} ha</b> del lote, el <b>{} %</b> presenta vegetación "
             "(<b>{} ha</b>) y el <b>{} %</b> corresponde a suelo (<b>{} ha</b>).{}</p>").format(
        fmt(fila[1]), fmt(fila[9], 1), fmt(fila[8]), fmt(fila[3], 1), fmt(fila[2]), texto_extra_rinde)
    return ("<html><head><style>{css}</style></head><body>{seccion}"
            "<h3 style=\"font-size:13pt\">5.{n} Ficha de lote: {nombre}</h3>"
            '<p align="center"><img src="img://mapa_lote" width="{w}"/></p>{ley}'
            "{tabla}<p>&nbsp;</p>{barra}{texto}"
            '<p class="nota">Archivo: {arch} · Tamaño de píxel: {px} cm</p>'
            "</body></html>").format(
        css=CSS, seccion=titulo_seccion, n=numero, nombre=_esc(nombre), w=ancho96, ley=_leyenda(), tabla=tabla, barra=_barra(fila),
        texto=texto, arch=_esc(os.path.basename(res["ruta"])), px=fmt(res["resolucion"][0] * 100, 1))


def _html_anexo(resultados, parametros, rangos, numero):
    crs = QgsProject.instance().crs()
    filas = "".join(
        '<tr><td>{}</td><td>{}</td><td align="right">{} × {}</td><td align="right">{}</td></tr>'.format(
            _esc(nombre_corto(r["nombre"])), _esc(os.path.basename(r["ruta"])),
            fmt(r["resolucion"][0] * 100, 2), fmt(r["resolucion"][1] * 100, 2), fmt(r["pixeles"], 0))
        for r in resultados)
    criterios = "".join("<li><b>{}:</b> {}</li>".format(_esc(NOMBRE[v]), _esc(rangos[v])) for v, _, _ in CLASES)
    metodo = ("Cada píxel de la imagen NDVI coloreada se clasificó según su tono de color (0° rojo, 60° amarillo, "
              "120° verde). Los píxeles transparentes (fuera del vuelo) se excluyen."
              if parametros["modo"] == "rgb" else
              "Cada píxel se clasificó según su valor de NDVI. Los píxeles sin dato se excluyen.")

    info_rinde = ""
    if parametros.get("habilitado"):
        info_rinde = (
            "<p><b>Parámetros de estimación de rendimiento:</b> Cultivo: {cultivo} · Estadio: {estadio}. "
            "Rindes de referencia: Alta vegetación {ra} t/ha, Baja vegetación {rb} t/ha, "
            "Suelo {rs} t/ha.</p>".format(
                cultivo=_esc(parametros.get("cultivo", "")),
                estadio=_esc(parametros.get("estadio_nombre", "")),
                ra=fmt(parametros.get("rinde_alta", 0.0), 2),
                rb=fmt(parametros.get("rinde_baja", 0.0), 2),
                rs=fmt(parametros.get("rinde_suelo", 0.0), 2),
            )
        )

    return ("<html><head><style>{css}</style></head><body>"
            "<h2>{n}. Anexo técnico: metodología y datos de origen</h2>"
            "<p><b>Origen de los datos:</b> {origen}.</p>"
            "<p><b>Método:</b> {metodo} La superficie de cada clase es la suma del área real de sus píxeles, "
            "calculada con el tamaño de píxel del ortomosaico{crs}.</p>"
            "<p><b>Criterios de clasificación:</b></p><ul>{criterios}</ul>"
            "{info_rinde}"
            "{limite}" + ESPACIO +
            '<table width="100%" cellspacing="0" cellpadding="4" border="0.5" '
            'style="border-collapse:collapse; border-color:#c8c8c8">'
            '<tr><th align="left">Lote</th><th align="left">Archivo</th>'
            '<th align="right">Píxel (cm)</th><th align="right">Píxeles válidos</th></tr>{filas}</table>'
            '<p class="nota">Informe generado con el complemento NDVI Cobertura para QGIS · {fecha}.</p>'
            "</body></html>").format(
        css=CSS, n=numero, origen=_esc(parametros["origen"]), metodo=metodo,
        crs=" (sistema de referencia {})".format(_esc(crs.authid())) if crs.isValid() else "",
        criterios=criterios,
        info_rinde=info_rinde,
        limite=("<p><b>Límite de cálculo:</b> {}.</p>".format(_esc(parametros["poligono"]))
                if parametros.get("poligono") else ""),
        filas=filas, fecha=_esc(parametros["fecha"]))


# ---------------------------------------------------------------- PDF
CABECERA_MM = 17
PIE_MM = 10
MAPA_GENERAL = (2200, 1500)
MAPA_LOTE = (2000, 1300)


def _documento(writer, html_texto, recursos=None):
    """QTextDocument maquetado sobre el PDF y paginado al alto útil de la hoja."""
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
                   "Informe de Cobertura Vegetal – NDVI")
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
        izquierda = "LaSierra 360 · Informe de cobertura NDVI"
        if self.datos.get("fecha_vuelo"):
            izquierda += " · Vuelo: {}".format(self.datos["fecha_vuelo"])
        caja = QRectF(0, y + _mm(1), self.ancho, _mm(5))
        p.drawText(caja, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, izquierda)
        p.drawText(caja, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "Página {} de {}".format(self.pagina, self.total))

    def terminar(self):
        self.painter.end()


def generar_pdf(ruta, resultados, parametros, rangos, datos, capas_fondo=(), capa_limite=None,
                fichas=True, progreso=None):
    """Escribe el informe en `ruta`. progreso(texto) se llama antes de cada paso lento."""
    def avisar(texto):
        if progreso is not None:
            progreso(texto)

    rp = parametros.get("rinde_params") or {
        "habilitado": parametros.get("habilitado", False),
        "rinde_alta": parametros.get("rinde_alta", 0.0),
        "rinde_baja": parametros.get("rinde_baja", 0.0),
        "rinde_suelo": parametros.get("rinde_suelo", 0.0),
    }
    filas = [fila_informe(r["nombre"], r["areas_m2"], rp) for r in resultados]
    hay_total = len(resultados) > 1
    if hay_total:
        filas.append(fila_informe("TOTAL", {c: sum(r["areas_m2"][c] for r in resultados)
                                            for c in (SUELO, BAJA, ALTA)}, rp))

    writer = QPdfWriter(ruta)
    writer.setResolution(DPI)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(15, 12, 15, 12), QPageLayout.Unit.Millimeter)
    writer.setTitle("Informe de Cobertura Vegetal NDVI")
    writer.setCreator("NDVI Cobertura – LaSierra 360")

    ancho = writer.width()
    ancho96 = int(ancho * 96 / DPI)  # QTextDocument mide <img width> en píxeles de 96 ppp

    capas = [capa_para_mapa(r) for r in resultados]
    validas = [c for c in capas if c is not None]
    contorno = capa_contorno(capa_limite)

    avisar("Generando mapa general…")
    recursos = {}
    if validas:
        recursos["img://mapa_general"] = renderizar_mapa(validas, validas, capas_fondo, contorno, *MAPA_GENERAL)

    # Se mide cuántas hojas ocupa cada parte antes de dibujar, para el "Página x de N".
    # Las fichas se miden con una imagen en blanco del mismo tamaño que su mapa.
    principal = _documento(writer, _html_principal(resultados, filas, parametros, rangos, datos,
                                                   hay_total, ancho96), recursos)
    anexo = _documento(writer, _html_anexo(resultados, parametros, rangos, 6 if fichas else 5))
    total = principal.pageCount() + anexo.pageCount()
    if fichas:
        blanco = QImage(MAPA_LOTE[0], MAPA_LOTE[1], QImage.Format.Format_RGB888)
        blanco.fill(QColor("#ffffff"))
        for i, res in enumerate(resultados, start=1):
            total += _documento(writer, _html_ficha(i, filas[i - 1], rangos, res, ancho96, parametros),
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
                        # Lote activo destacado; vecinos apagados con máscara oscura
                        img["img://mapa_lote"] = _renderizar_mapa_lote(
                            capa, validas, validas, capas_fondo, contorno, *MAPA_LOTE)
                    else:
                        # Solo hay un lote: renderizado normal sin máscara
                        img["img://mapa_lote"] = renderizar_mapa(
                            validas, [capa], capas_fondo, contorno, *MAPA_LOTE)
                pag.dibujar(_documento(writer, _html_ficha(i, filas[i - 1], rangos, res, ancho96, parametros), img))

        pag.dibujar(anexo)
    finally:
        pag.terminar()
    return pag.pagina
