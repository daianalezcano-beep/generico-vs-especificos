# Diseño: TRFPO (genérico) vs tarifarios reales de transportistas

Ver `BRIEF.md` para el pedido de negocio completo. Este documento registra
las decisiones de diseño tomadas durante la construcción (algunas
ajustan/precisan lo que dice `BRIEF.md` a partir de datos reales).

## Alcance de esta primera entrega

Fase 1 (extracción de tarifas vigentes) + Fase 2 (motor de matching) +
capa de conversión de moneda. Fase 3 (comparación de gap) y Fase 4
(salida Excel) quedan para una siguiente entrega — el archivo de config
de esta entrega (`tabla_bases_vehiculo_pax.csv`) ya está armado para
que Fase 3 lo consuma sin cambios de esquema.

## Reuso de las herramientas hermanas

Dos repos hermanos ya tienen automatización real de Selenium contra
Tourplan NX, cada uno resolviendo un problema distinto:

- `Tourplan-Valorizacion-EX-TF` (`tourplan_valorizacion_pkg_v3.py`):
  copia markup/commission de un PCM hacia el servicio madre. Su
  `buscar_producto` (Location/Supplier/Code/ServiceType) y su lectura de
  la grilla RATES **del propio componente** (`_leer_tabla_rates` /
  `_extraer_valores_ad`: pax range como columnas "N - M AD", con la
  columna GROUP COST) son exactamente lo que necesitamos para leer la
  tarifa vigente de un código TRFPO o de un código específico de
  transportista — es la única de las dos herramientas que opera al nivel
  de componente individual en vez de servicio madre/PCM.
- `cbd-impact-simulator-pkg` (`cbd_impact_simulator_pkg.py`): simula
  impacto de cambio de tipo de cambio recalculando vía un currency
  subcode de prueba en Tourplan. Tiene una versión más completa de
  `buscar_producto` (además de buscar, abre el resultado y confirma que
  quedó en el contexto correcto del producto — `_en_contexto_producto`),
  que es la que se reusa acá. El resto de esa herramienta (USED IN,
  PCM/Dashboard, cambio de subcode, recalculo, reversión) es exclusivo
  del problema de simulación de tipo de cambio y NO aplica a este
  proyecto — no hace falta simular nada, sólo leer la tarifa vigente tal
  como está cargada.

`extraccion_tarifas_vigentes.py` (Fase 1 de este repo) es la versión
reducida: `buscar_producto` + navegación a RATES + lectura de grilla,
sin PCM, sin USED IN, sin subcode, sin loop de períodos histórico — sólo
la tarifa vigente por rango de pax de cada código, para TRFPO y cada
transportista.

**Sin CSV/Excel de entrada**: a diferencia de los scripts hermanos (que
leen una lista de trabajo desde un Excel armado a mano, columna
PRODUCTOS), este script no necesita que se le arme ninguna lista de
códigos. `listar_codigos_supplier` — nueva, no viene de ningún script
hermano — reusa el llenado de filtros de `buscar_producto` pero deja el
campo Código vacío y lee TODA la grilla de resultados (código +
descripción de cada fila) en vez de abrir un único producto puntual.
Con eso, `descubrir_cola` recorre `COMPARACIONES` (Location + Supplier
genérico + lista de transportistas, editado directamente en el script)
y construye la cola de trabajo completa preguntándole a Tourplan mismo
qué códigos existen — no hace falta exportar el Product List de nadie
de antemano.

**Sin verificar contra Tourplan real todavía** — a diferencia de los
scripts hermanos (que ya corrieron y se depuraron contra Tourplan Test
varias veces, ver sus HISTORIAL/README), este script recién adaptado no
tiene una corrida real encima. Es esperable que la primera corrida real
encuentre ajustes de selector necesarios (mismo patrón que documentan
los hermanos) — no tomar el código como validado hasta esa primera
corrida. `listar_codigos_supplier` es la parte con menos precedente (no
viene adaptada de ningún script hermano): su heurística para distinguir
la celda de código de la celda de descripción en la grilla de
resultados, y su detección de paginación, son las que más probablemente
necesiten ajuste contra el DOM real.

## Moneda

Confirmado por la usuaria: cada período de la grilla RATES tiene
columnas **BUY CURRENCY** y **SELL CURRENCY**, cargadas iguales por
default — alcanza con leer una (`leer_tarifa_vigente_componente`
prioriza BUY, cae a SELL si no encuentra columna con "BUY" en el
header). Es distinto de lo que hacen los scripts hermanos: ninguno de
los dos lee moneda en ningún punto — `tourplan_valorizacion_pkg_v3.py`
no la necesita (copia markup, no valores) y `cbd_impact_simulator_pkg.py`
asume (confirmado por captura real en ese contexto) que el GROUP COST ya
está en USD. Acá la moneda SÍ puede variar por transportista (brief
original: "muchas veces los tarifarios de los transportistas están en
ARS mientras que el TRFPO está en USD"), así que se lee por línea en
vez de asumirse.

Conversión: sólo se completa `TARIFA_USD` cuando la moneda leída es
`USD` (se copia tal cual) o `ARS` (se divide por `TIPO_CAMBIO_ARS_USD`,
constante al principio de `extraccion_tarifas_vigentes.py` — un único
valor editado directamente en el script por la usuaria, sin CSV aparte,
igual que el subcode de prueba del simulador hermano). Si la moneda es
ARS pero `TIPO_CAMBIO_ARS_USD` quedó en `None`, o si la columna de
moneda no se encuentra en la grilla real (headers no confirmados
todavía contra Tourplan — ver más abajo), `TARIFA_USD` queda vacío en
vez de inventar un valor, y el script avisa por consola para que se
complete/revise a mano.

Sin confirmar todavía: el texto exacto del header en el DOM (¿dice
literalmente "BUY CURRENCY" o alguna variante como "Buy Ccy"?) y si el
valor de celda es el código ISO ("ARS"/"USD") o un nombre largo — ajustar
`idx_ccy` en `leer_tarifa_vigente_componente` en la primera corrida real
si no matchea.

## Motor de matching (Fase 2) — hallazgos reales al validar

Validado corriendo `test_matching.py` contra los 3 Product List reales
(TRFPO + Turismo El Puente + Houseman Cars, BUE) — ver ese script para
el detalle de casos. Dos ajustes que no estaban en el `BRIEF.md`
original y salieron de la corrida real:

1. **Categoría "Hr(s) Dispo" con cantidad de horas** (HRD3/HRD4/HRD5):
   sus descripciones dicen "3 Hrs Dispo"/"4 Hrs Dispo"/"5 Hrs Dispo"
   (plural, con número) — el keyword literal "Hr Dispo" del brief no
   matchea esas variantes por texto exacto, así que HRD319/HRD419/etc.
   quedaban en TRASLADO mientras que HRD (genérico, "Hr Dispo" singular)
   quedaba en EXCURSION — dos tramos de la misma familia de servicio con
   categoría distinta sin motivo de negocio. Corregido con regex
   `HRS?\s*DISPO` en vez de substring plano.
2. **Colisión `HRD`/`HRD4` genuinamente ambigua por texto solo**: el
   código `HRD42` (genérico + vehículo Bus, sufijo "42") literalmente
   empieza con el string "HRD4" (el prefijo TRFPO de "4 Hrs Dispo"),
   aunque semánticamente es "Hr Dispo" genérico + Bus, no "4 Hrs Dispo".
   El longest-prefix-match no puede distinguir esto sólo con el texto del
   código — por eso existe `COLISION_REVISAR`: este caso puntual queda
   marcado para que un humano lo confirme, en vez de asumir que el
   prefijo más largo siempre es semánticamente correcto.

Otros hallazgos de datos (no del algoritmo) que salieron de la corrida,
útiles para depurar el catálogo real en Tourplan si se quiere:

- `JCRE24` (Turismo El Puente, "Cruise Port - EZE Minibus 24 Japon")
  queda `SIN_MATCH`: el TRFPO real es `JCREZ`, no comparte prefijo
  literal con `JCRE24`.
- `JCT324` ("City 3hs - Japon (6/14 pax)") queda
  `SIN_VEHICULO_PARSEADO`: a diferencia de sus filas hermanas
  (`JCT319`/`JCT342`), a su descripción le falta el texto del vehículo.
