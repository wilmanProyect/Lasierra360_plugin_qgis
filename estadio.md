# Guía Técnica: Estadios Fenológicos y Estimación de Rendimiento NDVI

Complemento QGIS: **NDVI Cobertura** (v1.3.0)  
Desarrollado para: **LaSierra 360 / Rio Verde Sistema**

---

## 1. Escala Fenológica de Cultivos (VE a R8)

La escala incorporada corresponde al estándar de Ritchie (utilizada en soja y con correspondencias directas en maíz y otros cultivos de grano). Se divide en dos fases: **Vegetativa (V)** y **Reproductiva (R)**.

| Código | Estadio | Tipo | Descripción Agronómica |
| :---: | :--- | :---: | :--- |
| **VE** | Emergencia | Temprano | Emergencia de plántulas sobre la superficie del suelo. |
| **VC** | Cotiledonar | Temprano | Cotiledones completamente abiertos; hojas unifoliadas desplegadas. |
| **V1** | 1er nudo | Temprano | Primer par de hojas unifoliadas / primera hoja trifoliada abierta. |
| **V2** | 2do nudo | Temprano | Dos nudos con hojas completamente desplegadas. |
| **V3** | 3er nudo | Temprano | Tres nudos con hojas desplegadas; inicio de desarrollo radicular profundo. |
| **V4** | 4to nudo | Temprano | Cuatro nudos; inicio de ramificación lateral. |
| **V5** | 5to nudo | Vegetativo | Rápida acumulación de biomasa y área foliar. |
| **V6** | 6to nudo | Vegetativo | Cierre incipiente del entresurco; alta demanda de nitrógeno/nutrientes. |
| **V7** | 7mo nudo | Vegetativo | Canopeo en expansión activa. Fuerte indicador de vigor vegetativo. |
| **V8** | 8vo nudo | Vegetativo | Cobertura casi completa del suelo en distanciamientos estándar. |
| **V9** | 9no nudo | Vegetativo | Crecimiento vegetativo avanzado previo a la inducción floral. |
| **V10** | 10mo nudo | Vegetativo | Máximo porte vegetativo pre-reproductivo. |
| **R1** | Inicio de floración | Reproductivo | Una flor abierta en cualquier nudo del tallo principal. |
| **R2** | Floración plena | Reproductivo | Flor abierta en uno de los dos nudos superiores del tallo principal. |
| **R3** | Inicio formación de vainas | **Óptimo** | Vaina de 5 mm de longitud en uno de los 4 nudos superiores. |
| **R4** | Vainas desarrolladas | **Óptimo** | Vaina de 2 cm en uno de los 4 nudos superiores; definición del número de granos. |
| **R5** | Inicio llenado de grano | **Óptimo** | Semilla de 3 mm en una vaina de los nudos superiores; máxima demanda de fotosimilados. |
| **R6** | Grano / semilla completa | Reproductivo | Vaina que contiene una semilla verde que llena la cavidad del fruto. |
| **R7** | Inicio de maduración | Maduración | Una vaina normal sobre el tallo principal ha alcanzado el color de madurez. |
| **R8** | Madurez plena | Maduración | 95 % de las vainas con color típico de madurez. Cultivo listo para cosecha. |

---

## 2. Aplicabilidad del NDVI según el Estadio del Cultivo

El NDVI no estima rendimiento con la misma precisión en todas las etapas. La respuesta espectral del cultivo cambia a lo largo de su ciclo:

```
[VE — V4]               [V5 — V10]              [R1 — R2]             [R3 — R5]             [R6 — R8]
Emergencia temprana     Crecimiento vegetativo  Floración             Llenado de granos     Maduración/Senescencia
─────────────────────   ──────────────────────  ────────────────────  ────────────────────  ──────────────────────
❌ No rinde             🟡 Vigor/Biomasa        🟡 Potencial flores   🟢 VENTANA ÓPTIMA     🟠 Senescencia natural
(Fallas de siembra)     (Alta incert. clima)    (Período crítico)     (Máxima correlación)  (Pérdida de clorofila)
```

### 2.1. VE a V4 — Implantación y Emergencia
* **Aplicabilidad para rinde:** ❌ **No aplica.**
* **Utilidad del NDVI:** La señal del sensor está dominada por el suelo. Se utiliza para:
  * Evaluación de calidad de siembra y emergencia.
  * Detección de fallas de germinación, manchones por encharcamiento o daño por plagas de suelo.
  * Conteo y homogeneidad del rodal inicial.

### 2.2. V5 a V10 — Cierre de Canopeo y Crecimiento Vegetativo
* **Aplicabilidad para rinde:** 🟡 **Estimación orientativa / potencial inicial.**
* **Utilidad del NDVI:** Mide el vigor acumulado y el índice de área foliar (IAF). Permite identificar zonas con deficiencias nutricionales para fertilización correctiva variable, pero el rinde final aún depende fuertemente de las condiciones climáticas de floración.

### 2.3. R1 a R2 — Floración (Inicio del Período Crítico)
* **Aplicabilidad para rinde:** 🟡 **Media.**
* **Utilidad del NDVI:** Evalúa la capacidad de intercepción lumínica en el momento en que la planta define la cantidad de flores y estructuras reproductivas viables.

### 2.4. R3 a R5 — Formación de Vainas y Llenado de Grano (VENTANA ÓPTIMA)
* **Aplicabilidad para rinde:** 🟢 **MÁXIMA CORRELACIÓN ($R^2 > 0.75 - 0.85$).**
* **¿Por qué aquí?**
  1. El canopeo se encuentra completamente cerrado (mínima interferencia del suelo).
  2. La planta ya definió sus componentes de rendimiento: número de vainas/espigas por planta y número de granos por vaina.
  3. La tasa fotosintética del follaje en esta ventana se traduce directamente en peso final de los granos.

### 2.5. R6 a R8 — Maduración y Senescencia
* **Aplicabilidad para rinde:** 🟠 **No recomendada con NDVI estándar.**
* **Utilidad del NDVI:** Las hojas pierden clorofila de forma natural (se vuelven amarillas y caen). Un NDVI bajo en esta etapa indica madurez fisiológica normal y uniformidad de secado para cosecha, no una pérdida de producción.

---

## 3. Metodología de Cálculo Matemático

El plugin clasifica la superficie de cada ortomosaico píxel por píxel en tres clases de vigor y calcula la producción y el rendimiento medio:

### 3.1. Fórmulas por Lote

Dado un lote con áreas clasificadas:
* $A_{\text{suelo}}$: Hectáreas de suelo desnudo o sin vegetación.
* $A_{\text{baja}}$: Hectáreas de baja vegetación (vigor reducido / estrés).
* $A_{\text{alta}}$: Hectáreas de alta vegetación (vigor óptimo).
* $A_{\text{total}} = A_{\text{suelo}} + A_{\text{baja}} + A_{\text{alta}}$

Con rendimientos de referencia calibrados ($R_{\text{suelo}}$, $R_{\text{baja}}$, $R_{\text{alta}}$ en t/ha):

1. **Producción total estimada del lote ($P_{\text{lote}}$ en toneladas):**
   $$P_{\text{lote}} = (A_{\text{suelo}} \times R_{\text{suelo}}) + (A_{\text{baja}} \times R_{\text{baja}}) + (A_{\text{alta}} \times R_{\text{alta}})$$

2. **Rendimiento promedio estimado del lote ($R_{\text{lote}}$ en t/ha):**
   $$R_{\text{lote}} = \frac{P_{\text{lote}}}{A_{\text{total}}}$$

### 3.2. Fórmulas para el Total / Promedio General Ponderado

Para un conjunto de $N$ lotes analizados:

1. **Producción total acumulada ($P_{\text{total}}$ en toneladas):**
   $$P_{\text{total}} = \sum_{i=1}^{N} P_{\text{lote}, i}$$

2. **Rendimiento promedio general ponderado ($R_{\text{general}}$ en t/ha):**
   $$R_{\text{general}} = \frac{P_{\text{total}}}{\sum_{i=1}^{N} A_{\text{total}, i}}$$

> **Propiedad matemática:** Al ser una media ponderada por superficie, un lote grande influye proporcionalmente más en el rinde general que un lote pequeño, garantizando precisión contable para logística de cosecha.

---

## 4. Calibración de Datos en Campo (*Ground Truth*)

Para obtener la máxima precisión en las toneladas estimadas, se recomienda calibrar los valores de referencia mediante muestreo rápido en campo:

### Método de 2 Puntos de Control:
1. **Punto en Alta Vegetación (Verde):** Muestreo en un área representativa de alto vigor:
   * **Soja:** Conteo de plantas$/\text{m}^2 \times$ vainas/planta $\times$ granos/vaina $\times$ peso estimado de 1.000 granos (PMG).
   * **Maíz:** Espigas$/\text{m}^2 \times$ hileras/espiga $\times$ granos/hilera $\times$ PMG.
   * $\to$ Ingresar el resultado en **Alta veg. (verde)** (ej: $3.80\text{ t/ha}$).
2. **Punto en Baja Vegetación (Amarillo):** Muestreo en una zona con menor desarrollo o estrés hídrico:
   * $\to$ Ingresar el resultado en **Baja veg. (amarillo)** (ej: $2.10\text{ t/ha}$).
3. **Suelo (Rojo):** Mantener en $0.00\text{ t/ha}$ (caminos, fallas, lagunas).

---

## 5. Uso del Plugin en QGIS

1. **Abrir el complemento:** Desde el menú *Complementos -> NDVI Cobertura* o la barra de herramientas ráster.
2. **Seleccionar ortomosaicos:** Cargar las capas del proyecto o agregar archivos GeoTIFF.
3. **Pestaña `⚙️ Clasificación NDVI`:** Configurar el tipo de raster (RGB coloreado, monobanda o multiespectral) y los umbrales de clase.
4. **Pestaña `🌾 Rendimiento y Fenología`:**
   * Marcar la casilla **`[X] Habilitar estimación de rendimiento (t/ha) y producción total`**.
   * Seleccionar el **Cultivo** (Soja, Maíz, Trigo, Girasol, etc.).
   * Seleccionar el **Estadio fenológico** (observar la alerta del semáforo agronómico).
   * Ajustar los **Rindes de referencia** si se dispone de datos de campo.
5. **Calcular:** Presionar el botón **Calcular**.
6. **Exportar:**
   * **Copiar tabla:** Para pegar directamente en Excel con formato tabulado.
   * **Exportar CSV:** Incluye la tabla de lotes y los metadatos de calibración.
   * **Exportar HTML:** Informe interactivo para navegador web.
   * **Exportar informe PDF:** Documento formal con portada ejecutiva, KPIs, mapa general, mapa con foco por lote, tabla detallada, guía metodológica y anexo técnico.

