# -*- coding: utf-8 -*-
"""Cálculo de superficies por clase de ambiente topográfico / productivo.

Independiente de la interfaz de QGIS: sólo usa GDAL/OGR y numpy, y lee el
raster por ventanas alineadas a sus bloques para procesar ortomosaicos
y modelos digitales muy grandes sin agotar la memoria RAM.

Modos de análisis:
  "clases"   Raster con valores enteros de ambiente (1 = Zona Baja / Rojo,
             2 = Zona Media / Amarillo, 3 = Zona Alta / Verde). Típico de
             mapas ya clasificados (ej. capas CLAS).
  "rgb"      Imagen ya coloreada RGB (rojo -> amarillo -> verde). Cada píxel
             se clasifica por su tono HSV (0° rojo, 60° amarillo, 120° verde);
             los píxeles transparentes no se cuentan.
  "continuo" Raster continuo de elevación / cota / índice: clasifica según dos
             umbrales numéricos configurables (Bajo < umbral_bajo <= Media < umbral_alto <= Alta).
"""
import math
import os
import tempfile
import uuid

import numpy as np
from osgeo import gdal, ogr, osr

BAJA, MEDIA, ALTA = 1, 2, 3
CLASES = (
    (BAJA, "Zona Baja", "#d7191c"),
    (MEDIA, "Zona Media", "#fdd700"),
    (ALTA, "Zona Alta", "#1a9641"),
)

RADIO_TIERRA = 6371008.8  # radio medio en metros para coordenadas geográficas
PIXELES_POR_BLOQUE = 4_000_000
CROMA_MINIMA = 10  # píxeles neutros/grises/negros sin color definido


class ErrorAnalisis(Exception):
    pass


class Cancelado(ErrorAnalisis):
    pass


def _ventana(gt, xsize, ysize, envolvente):
    """Ventana de píxeles (xoff, yoff, ancho, alto) que cubre la envolvente (xmin, xmax, ymin, ymax)."""
    if gt[2] or gt[4]:  # raster rotado: se recorre completo
        return 0, 0, xsize, ysize
    xmin, xmax, ymin, ymax = envolvente
    cols = sorted(((xmin - gt[0]) / gt[1], (xmax - gt[0]) / gt[1]))
    filas = sorted(((ymax - gt[3]) / gt[5], (ymin - gt[3]) / gt[5]))
    x0 = max(0, int(math.floor(cols[0])))
    x1 = min(xsize, int(math.ceil(cols[1])))
    y0 = max(0, int(math.floor(filas[0])))
    y1 = min(ysize, int(math.ceil(filas[1])))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def _area_filas(gt, srs, y0, nfilas):
    """Área (m²) de un píxel en cada fila del bloque que empieza en la fila absoluta y0."""
    if srs is not None and srs.IsGeographic():
        filas = np.arange(y0, y0 + nfilas) + 0.5
        lat = np.radians(gt[3] + filas * gt[5])
        return abs(gt[1] * gt[5]) * (math.pi / 180.0) ** 2 * RADIO_TIERRA ** 2 * np.cos(lat)
    factor = srs.GetLinearUnits() if srs is not None else 1.0
    return np.full(nfilas, abs(gt[1] * gt[5] - gt[2] * gt[4]) * factor ** 2)


def _crear_mascara(geometrias_wkt, proyeccion, gt, ancho, alto):
    """Rasteriza los polígonos (en el SRC del raster) en un GeoTIFF temporal alineado a la ventana."""
    ruta = os.path.join(tempfile.gettempdir(), "ambiente_mascara_{}.tif".format(uuid.uuid4().hex))
    ds = gdal.GetDriverByName("GTiff").Create(
        ruta, ancho, alto, 1, gdal.GDT_Byte, ["COMPRESS=DEFLATE", "TILED=YES", "SPARSE_OK=TRUE"]
    )
    if ds is None:
        raise ErrorAnalisis("No se pudo crear la máscara temporal del polígono.")
    ds.SetGeoTransform(gt)
    srs = None
    if proyeccion:
        ds.SetProjection(proyeccion)
        srs = osr.SpatialReference()
        srs.ImportFromWkt(proyeccion)

    driver = "MEM" if int(gdal.VersionInfo()) >= 3110000 else "Memory"
    fuente = ogr.GetDriverByName(driver).CreateDataSource("")
    capa = fuente.CreateLayer("poligonos", srs, ogr.wkbUnknown)
    for wkt in geometrias_wkt:
        entidad = ogr.Feature(capa.GetLayerDefn())
        entidad.SetGeometry(ogr.CreateGeometryFromWkt(wkt))
        capa.CreateFeature(entidad)
    gdal.RasterizeLayer(ds, [1], capa, burn_values=[1])
    ds.FlushCache()
    return ds, ruta


def _leer(banda, nodata, x, y, ancho, alto):
    datos = banda.ReadAsArray(x, y, ancho, alto)
    if datos is None:
        raise ErrorAnalisis("Error de lectura del raster en la fila {}.".format(y))
    datos = datos.astype(np.float32, copy=False)
    validos = np.isfinite(datos)
    if nodata is not None and not math.isnan(nodata):
        validos &= datos != nodata
    return datos, validos


def tono(r, g, b):
    """Tono HSV en grados (rojo = 0, amarillo = 60, verde = 120) y croma (max - min)."""
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    croma = mx - mn
    c = np.where(croma == 0, 1.0, croma)
    h = np.where(mx == r, (g - b) / c,
                 np.where(mx == g, 2.0 + (b - r) / c, 4.0 + (r - g) / c)) * 60.0
    return h, croma


def bandas_por_defecto(ds):
    """Índices (R, G, B) según la interpretación de color; si no hay, 1, 2, 3."""
    interp = {ds.GetRasterBand(i).GetColorInterpretation(): i for i in range(1, ds.RasterCount + 1)}
    return (interp.get(gdal.GCI_RedBand, 1), interp.get(gdal.GCI_GreenBand, 2), interp.get(gdal.GCI_BlueBand, 3))


def analizar(ruta, modo="clases", banda=1, umbral_bajo=30.0, umbral_alto=90.0,
             valor_baja=1, valor_media=2, valor_alta=3,
             geometrias_wkt=None, ruta_clasificado=None, progreso=None):
    """Clasifica el raster de ambiente y calcula la superficie (m²) de cada zona.

    modo             "clases", "rgb" o "continuo".
    banda            número de banda a leer (base 1).
    umbral_bajo/alto en modo "rgb" son tonos en grados; en modo "continuo" son cotas/valores.
    valor_baja/media/alta valores numéricos que identifican las clases en modo "clases".
    geometrias_wkt   lista de geometrías WKT en el SRC del raster para limitar área (o None).
    ruta_clasificado si se indica, genera un GeoTIFF con 1=Baja, 2=Media, 3=Alta, 0=sin dato.
    progreso         callback(fraccion) -> True para cancelar.
    """
    ds = gdal.Open(ruta)
    if ds is None:
        raise ErrorAnalisis("No se pudo abrir el raster: {}".format(ruta))

    if modo == "rgb":
        if ds.RasterCount < 3:
            raise ErrorAnalisis("El modo 'imagen coloreada' necesita un raster RGB (3 o 4 bandas); "
                                "este archivo tiene {} banda(s).".format(ds.RasterCount))
        indices = bandas_por_defecto(ds)
    else:
        indices = (banda,)

    for i in indices:
        if i < 1 or i > ds.RasterCount:
            raise ErrorAnalisis("El raster tiene {} banda(s); no existe la banda {}.".format(ds.RasterCount, i))

    bandas = [(ds.GetRasterBand(i), ds.GetRasterBand(i).GetNoDataValue()) for i in indices]
    mascara_gdal = ds.GetRasterBand(indices[0]).GetMaskBand()
    usar_mascara_gdal = ds.GetRasterBand(indices[0]).GetMaskFlags() != gdal.GMF_ALL_VALID

    gt = ds.GetGeoTransform()
    proyeccion = ds.GetProjection()
    srs = None
    if proyeccion:
        srs = osr.SpatialReference()
        srs.ImportFromWkt(proyeccion)
    xsize, ysize = ds.RasterXSize, ds.RasterYSize

    x0, y0, ancho, alto = 0, 0, xsize, ysize
    mascara_ds = ruta_mascara = None
    salida = None

    try:
        if geometrias_wkt is not None:
            if not geometrias_wkt:
                raise ErrorAnalisis("No hay polígonos seleccionados para limitar el cálculo.")
            env = None
            for wkt in geometrias_wkt:
                e = ogr.CreateGeometryFromWkt(wkt).GetEnvelope()
                env = e if env is None else (min(env[0], e[0]), max(env[1], e[1]),
                                             min(env[2], e[2]), max(env[3], e[3]))
            ventana = _ventana(gt, xsize, ysize, env)
            if ventana is None:
                raise ErrorAnalisis("El polígono seleccionado no se superpone con el mapa de ambientes.")
            x0, y0, ancho, alto = ventana

        gt_ventana = (gt[0] + x0 * gt[1] + y0 * gt[2], gt[1], gt[2],
                      gt[3] + x0 * gt[4] + y0 * gt[5], gt[4], gt[5])

        if geometrias_wkt is not None:
            mascara_ds, ruta_mascara = _crear_mascara(geometrias_wkt, proyeccion, gt_ventana, ancho, alto)

        if ruta_clasificado:
            salida = gdal.GetDriverByName("GTiff").Create(
                ruta_clasificado, ancho, alto, 1, gdal.GDT_Byte, ["COMPRESS=DEFLATE", "TILED=YES"]
            )
            if salida is None:
                raise ErrorAnalisis("No se pudo crear el raster clasificado: {}".format(ruta_clasificado))
            salida.SetGeoTransform(gt_ventana)
            if proyeccion:
                salida.SetProjection(proyeccion)
            salida.GetRasterBand(1).SetNoDataValue(0)

        # Ventanas alineadas a los bloques internos
        bw, bh = bandas[0][0].GetBlockSize()
        filas_bloque = max(bh, min(alto, max(1, PIXELES_POR_BLOQUE // max(ancho, 1))) // bh * bh)
        cols_bloque = ancho
        if filas_bloque * ancho > PIXELES_POR_BLOQUE * 2:
            cols_bloque = max(bw, PIXELES_POR_BLOQUE // filas_bloque // bw * bw)
        ventanas = [(c, f) for f in range(0, alto, filas_bloque) for c in range(0, ancho, cols_bloque)]

        areas = {BAJA: 0.0, MEDIA: 0.0, ALTA: 0.0}
        pixeles = 0

        for n_ventana, (col, fila) in enumerate(ventanas):
            n = min(filas_bloque, alto - fila)
            m = min(cols_bloque, ancho - col)
            x, y = x0 + col, y0 + fila

            if modo == "rgb":
                rgb = [b.ReadAsArray(x, y, m, n) for b, _ in bandas]
                if any(a is None for a in rgb):
                    raise ErrorAnalisis("Error de lectura del raster en la fila {}.".format(y))
                datos, croma = tono(*(a.astype(np.float32) for a in rgb))
                validos = (croma >= CROMA_MINIMA) & (datos < 180.0)
            else:
                datos, validos = _leer(bandas[0][0], bandas[0][1], x, y, m, n)

            if usar_mascara_gdal:
                validos &= mascara_gdal.ReadAsArray(x, y, m, n) >= 128
            if mascara_ds is not None:
                validos &= mascara_ds.GetRasterBand(1).ReadAsArray(col, fila, m, n).astype(bool)

            clase = np.zeros(datos.shape, np.uint8)

            if modo == "clases":
                # Mapeo directo por valores numéricos de clase (por defecto 1, 2, 3)
                v_round = np.rint(datos)
                clase[validos & (v_round == valor_baja)] = BAJA
                clase[validos & (v_round == valor_media)] = MEDIA
                clase[validos & (v_round == valor_alta)] = ALTA
                validos = validos & (clase > 0)
            elif modo == "rgb":
                # Tono: rojo < umbral_bajo <= amarillo < umbral_alto <= verde
                clase[validos & (datos < umbral_bajo)] = BAJA
                clase[validos & (datos >= umbral_bajo) & (datos < umbral_alto)] = MEDIA
                clase[validos & (datos >= umbral_alto)] = ALTA
            else:  # continuo (ej. cota / elevación)
                clase[validos & (datos < umbral_bajo)] = BAJA
                clase[validos & (datos >= umbral_bajo) & (datos < umbral_alto)] = MEDIA
                clase[validos & (datos >= umbral_alto)] = ALTA

            area_filas = _area_filas(gt, srs, y, n)
            for c in areas:
                areas[c] += float(np.dot((clase == c).sum(axis=1), area_filas))
            pixeles += int((clase > 0).sum())

            if salida is not None:
                salida.GetRasterBand(1).WriteArray(clase, col, fila)

            if progreso is not None and progreso((n_ventana + 1) / len(ventanas)):
                raise Cancelado("Proceso cancelado por el usuario.")

        if salida is not None:
            niveles = [f for f in (2, 4, 8, 16, 32, 64, 128) if max(ancho, alto) / f >= 256]
            if niveles:
                salida.BuildOverviews("NEAREST", niveles)
    except Exception:
        if salida is not None:
            salida = None
            gdal.GetDriverByName("GTiff").Delete(ruta_clasificado)
        raise
    finally:
        mascara_ds = None
        if ruta_mascara:
            gdal.GetDriverByName("GTiff").Delete(ruta_mascara)
        if salida is not None:
            salida.FlushCache()
            salida = None
        ds = None

    return {
        "ruta": ruta,
        "modo": modo,
        "areas_m2": areas,
        "area_total_m2": sum(areas.values()),
        "pixeles": pixeles,
        "resolucion": (abs(gt[1]), abs(gt[5])),
        "geografico": bool(srs is not None and srs.IsGeographic()),
        "sin_src": srs is None,
        "ruta_clasificado": ruta_clasificado,
    }

