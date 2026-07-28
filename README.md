# generico-vs-especificos

Herramienta para comparar el valor cargado en el supplier genérico
**TRFPO** (Transporte Por Asignar) contra los tarifarios reales vigentes
de cada transportista, en Tourplan NX. Ver `BRIEF.md` para el pedido de
negocio completo y `DISENO.md` para las decisiones de diseño y hallazgos
de la validación contra datos reales.

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
  de una sola vez. Validado con varias corridas reales contra Tourplan
  Test (última: 116 códigos TRFPO + 52 de 6HOUS1 en BUE, matching limpio
  en 204/208 filas) — bugs reales encontrados y corregidos en el camino,
  ver DISENO.md.
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
  `COMPARACIONES`, `TIPO_CAMBIO_ARS_USD`, `PERIODO_ANALISIS_DESDE/HASTA`
  y `PRICE_CODE_DEFAULT` (todo al principio del archivo) antes de
  correr. `MOSTRAR_CAPTURAS=True` muestra las capturas inline si se
  corre en Colab/Jupyter (opcional). Salida: `tarifas_vigentes.xlsx`
  (una fila por código + período + rango de pax) y
  `comparacion_gap.xlsx` (el gap TRFPO vs. cada transportista).
- `comparacion_gap.py` — la misma Fase 3, como script standalone
  (pandas) para re-correr SÓLO la comparación sobre un
  `tarifas_vigentes.xlsx` ya existente, sin volver a scrapear Tourplan.
  Uso: `python comparacion_gap.py [tarifas_vigentes.xlsx] [salida.xlsx]`.
- `config/tabla_bases_vehiculo_pax.csv` — vehículo según rango de pax,
  por location/categoría/guía (editable, sin hardcodear en código). Ya
  no lo usa Fase 3 (ver DISENO.md) — queda por si hace falta en Fase 4.
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
