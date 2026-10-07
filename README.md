# generico-vs-especificos

Herramienta para comparar el valor cargado en un supplier genérico
(**TRFPO** — Transporte Por Asignar, **PEAPO**, **GUIAPO**, o cualquier
otro que se agregue) contra los tarifarios reales vigentes de cada
proveedor específico, en Tourplan NX. `COMPARACIONES` admite cualquier
cantidad de relaciones genérico/específico independientes (ver
`extraccion_tarifas_vigentes.py`) — no está atado a TRFPO. Ver
`BRIEF.md` para el pedido de negocio original (enfocado en TRFPO/
transportistas) y `DISENO.md` para las decisiones de diseño, la
generalización a PEAPO/GUIAPO y los hallazgos de la validación contra
datos reales.

## Estado actual

- ✅ **Fase 2 — motor de matching** (`matching_engine.py`): longest-prefix-match
  código específico → TRFPO, parseo de vehículo/rango de pax desde
  `Description`, banderas (JAPON/CRUCERO/SIN_GUIA/CASO_ESPECIAL_SIB/
  NO_TRANSPORTE), clasificación por categoría, indexado por
  `(LOCATION, CATEGORIA, GUIA, PAX)` contra la tabla de bases. Validado
  con los 3 Product List reales en `muestras/` — correr
  `python test_matching.py`.
- ✅ **Fase 1 + Fase 3 en una sola corrida**
  (`extraccion_tarifas_vigentes.py`): extrae tarifas vigentes de
  Tourplan real Y calcula la comparación de gap al final, en la misma
  ejecución — genera `tarifas_vigentes.xlsx` y `comparacion_gap.xlsx`
  de una sola vez. `COMPARACIONES` soporta cualquier cantidad de
  relaciones genérico/específico (TRFPO, PEAPO, GUIAPO, o las que se
  agreguen) en la misma corrida. Validado con varias corridas reales
  contra Tourplan Test para TRFPO (última: 116 códigos TRFPO + 52 de
  6HOUS1 en BUE, matching limpio en 204/208 filas) — bugs reales
  encontrados y corregidos en el camino, ver DISENO.md. PEAPO/GUIAPO
  están soportados en el código pero todavía sin validar contra
  Tourplan real. El gap se calcula por VEHÍCULO (sufijo de código +
  categoría/guía parseadas de la Description, contra
  `config/tabla_bases_vehiculo_pax.csv`), no por el pax bruto que
  reportó el específico — ver DISENO.md "Bug real: matching por
  vehículo".
- ⏳ Fase 4 (salida Excel final con el gap resaltado): pendiente.

## Estructura

- `BRIEF.md` — brief de negocio original.
- `DISENO.md` — decisiones de diseño y hallazgos de validación.
- `matching_engine.py` — motor de matching (Fase 2), puro pandas/regex.
- `test_matching.py` — corrida de validación contra `muestras/`.
- `extraccion_tarifas_vigentes.py` — **el script que corrés**. Extrae
  tarifa vigente por código (Fase 1), Selenium contra Tourplan NX, y al
  final calcula la comparación de gap (Fase 3) automáticamente, todo en
  una sola ejecución. No necesita CSV/Excel de entrada — busca los
  códigos él mismo en Tourplan. Editar `USERNAME`, `PASSWORD`,
  `COMPARACIONES`, `TIPO_CAMBIO_ARS_USD`, `PERIODO_ANALISIS_DESDE/HASTA`,
  `PRICE_CODE_DEFAULT` y `MODO` (todo al principio del archivo) antes de
  correr. `MOSTRAR_CAPTURAS=True` muestra las capturas inline si se
  corre en Colab/Jupyter (opcional). Salida: `tarifas_vigentes.xlsx`
  (una fila por código + período + rango de pax) y
  `comparacion_gap.xlsx` (el gap de cada relación genérico/específico,
  con `DIFERENCIA_PCT` formateado con signo %).
  - `COMPARACIONES` es una lista de relaciones genérico/específico
    independientes — no está atada a TRFPO. Cada elemento trae su
    propia `location`, `service_type`, `generico`, `especificos`
    (lista de códigos de proveedor específico) y `price_code`. Ya trae
    la relación TRFPO vs. transportistas y ejemplos comentados de
    PEAPO (`service_type: "PJ"`) y GUIAPO (`service_type: "GU"`) —
    para agregar una relación nueva alcanza con sumar un elemento más
    a la lista, sin tocar el resto del script.
  - `MODO = "COMPLETO"` (default): extrae genérico(s) + específicos.
  - `MODO = "SOLO_GENERICO"`: extrae sólo los genéricos de
    `COMPARACIONES` y los guarda en
    `tarifas_generico_<GENERICO>_<LOCATION>.xlsx` para reusar después —
    correr una vez cuando el genérico cambie (1-2 veces al año), no en
    cada corrida.
  - `MODO = "SOLO_ESPECIFICOS"`: extrae sólo los específicos listados y
    arma la comparación contra los genéricos ya guardados (falla con un
    error claro si no corriste `SOLO_GENERICO`/`COMPLETO` antes para
    esa combinación de genérico + location).
  - ⚠️ Si corrés esto en **Google Colab** y vas a usar `SOLO_GENERICO`
    un día y `SOLO_ESPECIFICOS` en una sesión posterior (el caso
    normal, ya que un genérico se mantiene fijo bastante tiempo): el
    disco de Colab no persiste entre sesiones. Poné `CACHE_DIR`
    apuntando a Google Drive (ej.
    `"/content/drive/MyDrive/generico-vs-especificos"`) para que
    `tarifas_generico_<GENERICO>_<LOCATION>.xlsx` sobreviva — el script
    monta Drive solo, no hace falta un `drive.mount(...)` en otra
    celda (la primera vez en un navegador/cuenta nueva, Colab igual va
    a pedir un click de autorización — eso es de Google, no se puede
    saltear).
- `comparacion_gap.py` — la misma Fase 3, como script standalone
  (pandas) para re-correr SÓLO la comparación sobre un
  `tarifas_vigentes.xlsx` ya existente, sin volver a scrapear Tourplan.
  Uso: `python comparacion_gap.py [tarifas_vigentes.xlsx] [salida.xlsx]`.
- `extraccion_vigencias.py` — relevamiento de **vigencias** (no de
  tarifas: no lee ningún valor de costo). Dado un supplier/location/
  service type (o un código puntual), busca sus product codes en
  Tourplan, entra a RATES de cada uno y exporta las columnas de la
  LISTA de períodos (Rate Period, PC, Buy/Sell Currency, Sale Period,
  Rate Status, Rate Text, Rate Name) **sin abrir ningún período** —
  pensado para identificar rápido qué vigencias necesitan actualización
  (períodos vencidos, sin cargar, con estado no confirmado, etc.), no
  para leer costos por rango de pax. A diferencia de
  `extraccion_tarifas_vigentes.py`, usa un Excel de entrada como cola
  de trabajo (hoja `PRODUCTOS`, ESTADO/OBSERVACIONES, guardado fila a
  fila y resumible — ver skill `armando-excel-como-cola-de-trabajo`),
  no una lista editada al principio del script. Ninguno de
  LOCATION/SUPPLIER/SERVICE TYPE/CODIGO es obligatorio por sí solo
  (vacío = "todos" en esa dimensión), pero deben venir completos al
  menos 2 de esos 4 campos. Por fila: si RATE FROM/RATE TO quedan
  vacíos, exporta sólo el primer período de la lista tal cual la
  entrega Tourplan (el más reciente/"último", sin ordenar nada); si se
  completan, exporta TODOS los períodos que se solapen con ese rango.
  Salida: hoja `VIGENCIAS` en el mismo archivo, una fila por período
  exportado. Corriendo el script sin ningún Excel todavía creado, lo
  genera con una fila de ejemplo y comentarios por columna.
- `config/tabla_bases_vehiculo_pax.csv` — vehículo según rango de pax,
  por location/categoría/guía (editable, sin hardcodear en código).
  Fase 3 la usa para elegir el tramo de TRFPO que le corresponde a cada
  código específico según su vehículo (por sufijo de código, ej. "19" =
  Sprinter 19) y categoría/guía (parseadas de la Description), en vez
  de comparar contra el PAX_DESDE/HASTA que reportó el específico en su
  propia tarifa — ver DISENO.md "Bug real: matching por vehículo".
  `comparacion_gap.py` la lee directo de este archivo;
  `extraccion_tarifas_vigentes.py` (celda única de Colab, sin archivos
  hermanos disponibles) tiene su propia copia embebida — mantenerlas
  sincronizadas si se edita el CSV.
- `muestras/` — 3 Product List reales (TRFPO + 2 transportistas, BUE)
  usados para validar el matching.

## Uso rápido

```bash
pip install pandas openpyxl
python test_matching.py                              # valida el matching contra las muestras
python comparacion_gap.py tarifas_vigentes.xlsx       # Fase 3, sobre una salida real de Fase 1
```

Para correr la extracción real (Fase 1) hace falta Selenium + Chrome y
credenciales de Tourplan Test — ver cabecera de
`extraccion_tarifas_vigentes.py`.
