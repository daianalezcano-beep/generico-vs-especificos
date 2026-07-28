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
- ⚠️ **Fase 1 — extracción de tarifas vigentes**
  (`extraccion_tarifas_vigentes.py`): en depuración activa con corridas
  reales contra Tourplan Test — varios bugs reales ya corregidos (ver
  DISENO.md), sospecha de paginación sin confirmar todavía en
  `listar_codigos_supplier`.
- ⚠️ **Fase 3 — comparación de gap** (`comparacion_gap.py`): matchea
  cada código de transportista contra su TRFPO (reusa
  `matching_engine.py`) y calcula la diferencia en USD. Probado contra
  datos reales — su precisión depende de que Fase 1 haya descubierto
  TODOS los códigos TRFPO (ver hallazgo de paginación en DISENO.md).
- ⏳ Fase 4 (salida Excel final con el gap resaltado): pendiente.

## Estructura

- `BRIEF.md` — brief de negocio original.
- `DISENO.md` — decisiones de diseño y hallazgos de validación.
- `matching_engine.py` — motor de matching (Fase 2), puro pandas/regex.
- `test_matching.py` — corrida de validación contra `muestras/`.
- `extraccion_tarifas_vigentes.py` — extracción de tarifa vigente por
  código (Fase 1), Selenium contra Tourplan NX. No necesita CSV/Excel de
  entrada — busca los códigos él mismo en Tourplan. Editar `USERNAME`,
  `PASSWORD`, `COMPARACIONES`, `TIPO_CAMBIO_ARS_USD`,
  `PERIODO_ANALISIS_DESDE/HASTA` y `PRICE_CODE_DEFAULT` (todo al
  principio del archivo) antes de correr. `MOSTRAR_CAPTURAS=True`
  muestra las capturas inline si se corre en Colab/Jupyter (opcional).
  Salida: `tarifas_vigentes.xlsx` (una fila por código + período + rango
  de pax).
- `comparacion_gap.py` — comparación de gap (Fase 3), pandas puro. Uso:
  `python comparacion_gap.py [tarifas_vigentes.xlsx] [salida.xlsx]`.
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
