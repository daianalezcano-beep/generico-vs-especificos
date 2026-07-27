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
  (`extraccion_tarifas_vigentes.py`): adaptado de los scripts hermanos
  (`buscar_producto` + lectura de grilla RATES del componente), pero
  **sin verificar contra Tourplan real todavía** — ver DISENO.md.
- ⏳ Fase 3 (comparación de gap, con conversión de moneda) y Fase 4
  (salida Excel): pendientes.

## Estructura

- `BRIEF.md` — brief de negocio original.
- `DISENO.md` — decisiones de diseño y hallazgos de validación.
- `matching_engine.py` — motor de matching (Fase 2), puro pandas/regex.
- `test_matching.py` — corrida de validación contra `muestras/`.
- `extraccion_tarifas_vigentes.py` — extracción de tarifa vigente por
  código (Fase 1), Selenium contra Tourplan NX. Editar `USERNAME`,
  `PASSWORD` y `PRODUCT_LIST_CSVS` antes de correr.
- `config/tabla_bases_vehiculo_pax.csv` — vehículo según rango de pax,
  por location/categoría/guía (editable, sin hardcodear en código).
- `config/transportistas_moneda.csv` — moneda por transportista y tipo
  de cambio ARS→USD a aplicar en Fase 3 (editable, ver DISENO.md).
- `muestras/` — 3 Product List reales (TRFPO + 2 transportistas, BUE)
  usados para validar el matching.

## Uso rápido

```bash
pip install pandas
python test_matching.py          # valida el matching contra las muestras
```

Para correr la extracción real (Fase 1) hace falta Selenium + Chrome y
credenciales de Tourplan Test — ver cabecera de
`extraccion_tarifas_vigentes.py`.
