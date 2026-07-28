# Diseño: TRFPO (genérico) vs tarifarios reales de transportistas

Ver `BRIEF.md` para el pedido de negocio completo. Este documento registra
las decisiones de diseño tomadas durante la construcción (algunas
ajustan/precisan lo que dice `BRIEF.md` a partir de datos reales).

## Estado al 2026-07-27 — resumen para retomar

**Fase 2 (matching)**: terminada y validada contra los 3 Product List
reales de `muestras/` (`python test_matching.py`). No tiene pendientes
abiertos.

**Fase 1 (extracción, `extraccion_tarifas_vigentes.py`)**: en corridas
reales contra Tourplan Test, no simulada. Bugs reales encontrados y ya
corregidos en el código (quedan documentados más abajo en este archivo,
sección por sección):
1. Colab no trae Chrome preinstalado → se agregó detección/instalación
   automática (`_find_chrome`/`_instalar_chrome`).
2. La heurística de columnas de `listar_codigos_supplier` confundía la
   celda de Location ("BUE") con la de Code → se pasó a identificar la
   columna por header ("Code") en vez de por forma del texto.
3. Abrir RATES no muestra la grilla de tarifas directo — muestra antes
   una lista de períodos → se agregó navegación de períodos
   (`_leer_periodos_rates`, `_periodos_en_rango`), con filtro configurable
   por rango de fechas (`PERIODO_ANALISIS_DESDE/HASTA`) y por Price Code
   (`PRICE_CODE_DEFAULT`, para evitar leer la vista agregada "All Price
   Codes" que puede devolver 0.0 sin avisar).
4. La moneda no está en la grilla de un período ya abierto (headers
   reales: `"USD\nGROUP COST"`, embebida en el texto) → se movió la
   lectura a la lista de períodos (misma fila que fecha/price code).

**Último archivo de resultados real recibido** (corrida ANTERIOR al fix
#4 de moneda, con `LIMIT_PRUEBA=5`): sólo trajo códigos de TRFPO (4
códigos × 7 rangos de pax = 28 filas) — es esperado, no un bug: con
`LIMIT_PRUEBA` bajo, la cola se agota dentro del primer supplier
(genérico) antes de llegar a los transportistas, porque
`descubrir_cola` procesa "generico" primero y recién después cada
transportista de la lista. `MONEDA`/`TARIFA_USD` vinieron vacíos en ese
archivo — esperado también, es la corrida de antes del fix #4.

Confirmado por la usuaria: los rangos que llegan hasta 9999 (el
"catch-all" de pax abierto hacia arriba, ej. "42-9999 AD") siempre
están en 0 — no es un error de lectura, así se carga en Tourplan. El
otro dato (36-41 sin valor) sigue sin confirmar todavía.

Confirmado por la usuaria: `600TRF`/`700TRF` (y `MINWAT`, mismo caso)
no son tarifas de transporte real — están cargados en 0 a mano, como
placeholder. Se agregó `CODIGOS_EXCLUIR` en
`extraccion_tarifas_vigentes.py`: `descubrir_cola` los descarta ANTES
de abrir el producto (no gasta tiempo de Selenium en ellos), tanto por
código explícito como por texto de descripción
(`PATRONES_EXCLUIR_DESCRIPCION` — mismo criterio que `NO_TRANSPORTE` en
`matching_engine.py`, sin cross-importar ese módulo para no romper la
convención de "un solo archivo" de este script).

**Fase 1 NO compara nada contra el transportista específico** — sólo
extrae la tarifa de cada código, sea TRFPO o transportista, en filas
separadas. Cruzar "este código de transportista corresponde a este
código TRFPO, ¿cuánto difieren?" es exactamente el trabajo de Fase 3
(comparación de gap), que todavía no se construyó — necesita juntar la
salida de Fase 2 (`matching_engine.py`, quién matchea con quién) con la
salida de Fase 1 (`tarifas_vigentes.xlsx`, cuánto vale cada uno).

**Para la próxima corrida**: subir o quitar `LIMIT_PRUEBA` para que la
cola llegue a los transportistas y no sólo a TRFPO, y confirmar que
`MONEDA`/`TARIFA_USD` ya salen bien con el fix de períodos.

**Pendiente sin empezar**: Fase 3 (comparación de gap, usando
`config/tabla_bases_vehiculo_pax.csv` para el join por vehículo/pax) y
Fase 4 (Excel de salida final, con formato/resaltado de diferencias).

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

## Paginación en listar_codigos_supplier (sospecha fuerte, no confirmada)

Corriendo `comparacion_gap.py` (ver más abajo) contra datos reales:
TRFPO devolvió sólo **22 códigos** en BUE, y de los 52 códigos de
6HOUS1, **172 de 208 filas** no encontraron ningún prefijo TRFPO — pero
mirando los códigos uno por uno, la mayoría SON rutas de transporte
normales (`EZHTAU`, `HTAEAU`, `HRDAU`, `FARAU`, etc.) que en las
muestras (`muestras/Product_List_TRFPO.csv`) sí tienen su TRFPO
correspondiente (`EZHT`, `HTAE`, `HRD`, `FAR`...). 22 es un número
sospechosamente redondo para ser el catálogo completo de TRFPO en BUE
— la hipótesis más probable es que la grilla de resultados pagina, y
`listar_codigos_supplier` sólo estaba leyendo la primera página.

Se agregó manejo de paginación (`_pasar_pagina_siguiente` +
`_scrapear_pagina_resultados` en loop, acumulando por código hasta que
no aparecen códigos nuevos o no hay botón de "siguiente"). **El
selector del botón de paginación NO está confirmado contra el DOM
real** — es una heurística genérica (texto/aria-label con
"next"/"siguiente"/">" dentro de algo con clase "pagin"/"pager", no
deshabilitado). Verificar en la próxima corrida si ahora TRFPO trae
más de 22 códigos; si sigue en 22, revisar si el heurístico de
"siguiente" no está encontrando el botón real de Tourplan.

## Períodos y Price Code

**Hallazgo real de la primera corrida**: abrir RATES no muestra
directamente la grilla de tarifas — muestra primero una LISTA de
períodos (cada uno con su rango de fechas), y hay que abrir el período
correcto con un clic antes de llegar a la grilla con la columna COST.
El diseño original asumía (sin confirmar) que la grilla aparecía
directa; no era así.

Para esto se reusan, tal cual, las funciones que
`tourplan_valorizacion_pkg_v3.py` ya tenía resueltas y confirmadas
contra Tourplan real para este mismo problema (se ocupa de leer/abrir
períodos al escribir rates de servicios madre): `_leer_periodos`
(selectores `td.tpcol-rateperiod` para la fecha del período y
`td.tpcol-pricecodecode` para su price code) y `_seleccionar_price_code`
(radio `#priceCodeModeSelected` + combo `#priceCode`, para pasar de la
vista agregada "All Price Codes" a un price code puntual).

**Por qué filtrar por Price Code, no sólo por fecha**: el hallazgo más
importante de esa función hermana es que la vista "All Price Codes"
(la que se ve por default) es una vista AGREGADA — lo que se lee ahí
puede no ser el valor persistido, y en el caso del script hermano se
leía **0.0 sin ningún error visible**. Por eso `PRICE_CODE_DEFAULT =
"TR"` filtra a un price code concreto antes de leer, en vez de confiar
en la vista default. Si en la corrida real los períodos de estos
supplier no tienen price codes separados (todo "Unassigned"/"ALL"),
poner `PRICE_CODE_DEFAULT = "ALL"` para desactivar el filtro sin que
rompa nada.

**Rango de análisis**: `PERIODO_ANALISIS_DESDE`/`PERIODO_ANALISIS_HASTA`
(ninguno de los dos existía en el diseño original — se agregaron a
pedido de la usuaria) definen qué períodos procesar: se toman TODOS los
que se superponen con ese rango, no sólo uno — un código puede tener
más de un período cargado dentro del rango pedido, y cada uno queda
como fila(s) separada(s) en la salida (columnas `PERIODO_DESDE`,
`PERIODO_HASTA`, `PRICE_CODE`). Si ambos quedan en `None`, el rango es
"hoy" (un solo día) — o sea, sólo el período vigente en este momento.

**Sin confirmar todavía**: si después de abrir un período hace falta
algo más que `_seleccionar_price_code` para que la grilla persistida
se muestre (el script hermano lo hace sobre un servicio madre/PCM, no
sobre un componente suelto — puede haber diferencias de UI); y si
`listar_codigos_supplier`/`buscar_producto` dejan al producto
realmente "en contexto" antes de este paso (si RATES muestra la lista
de períodos de OTRO producto por error, sería un síntoma de eso, no de
esta parte).

## Moneda

Confirmado por la usuaria: hay columnas **BUY CURRENCY**/**SELL
CURRENCY** (cargadas iguales por default, alcanza con leer una), pero
**en la pantalla donde se listan todos los períodos** (`td.tpcol-
rateperiod`), no dentro de la grilla de un período ya abierto —
corregido tras una corrida real: al abrir un período, la grilla NO
tiene esas columnas; la moneda aparece ahí sólo embebida en el texto
del header (ej. `"USD\nGROUP COST"`), que no es la fuente principal
(se usa como fallback en `_detectar_moneda_headers` si la lista de
períodos no trae la columna). `_leer_periodos_rates` lee la moneda por
fila de período (misma tabla que ya lee fecha/price code) y
`leer_tarifa_vigente_componente` la propaga a cada fila AD de ese
período vía `_extraer_filas_ad(..., moneda_periodo=...)`.

Es distinto de lo que hacen los scripts hermanos: ninguno de los dos
lee moneda en ningún punto — `tourplan_valorizacion_pkg_v3.py` no la
necesita (copia markup, no valores) y `cbd_impact_simulator_pkg.py`
asume (confirmado por captura real en ese contexto) que el GROUP COST
ya está en USD. Acá la moneda SÍ puede variar por transportista (brief
original: "muchas veces los tarifarios de los transportistas están en
ARS mientras que el TRFPO está en USD"), así que se lee por línea en
vez de asumirse.

Conversión: sólo se completa `TARIFA_USD` cuando la moneda leída es
`USD` (se copia tal cual) o `ARS` (se divide por `TIPO_CAMBIO_ARS_USD`,
constante al principio de `extraccion_tarifas_vigentes.py` — un único
valor editado directamente en el script por la usuaria, sin CSV aparte,
igual que el subcode de prueba del simulador hermano). Si la moneda es
ARS pero `TIPO_CAMBIO_ARS_USD` quedó en `None`, o si no se pudo
determinar la moneda (ni en la lista de períodos ni en el fallback de
header), `TARIFA_USD` queda vacío en vez de inventar un valor, y el
script avisa por consola para que se complete/revise a mano.

Sin confirmar todavía: el texto exacto del header de la columna de
moneda en la lista de períodos (¿dice literalmente "BUY CURRENCY" o
alguna variante como "Buy Ccy"?) — ajustar el regex en
`_leer_periodos_rates` en la primera corrida real si no matchea. El
fallback de `_detectar_moneda_headers` (headers de la grilla ya
abierta, ej. `"USD\nGROUP COST"`) sí está confirmado contra datos
reales (12HT, ver console log de la corrida).

## Parseo de números (bug real: ARS de 6HOUS1 leía None/0 siempre)

Confirmado con datos reales: los códigos de 6HOUS1 (ARS) traían
`TARIFA_VIGENTE=None` en el rango "1-2 AD" (donde está el valor real
que la usuaria SÍ ve en pantalla) y `0` en "3-9999" (catch-all, 0
esperado) — en el 100% de sus ~50 códigos, siempre el mismo patrón.
Causa: el parseo de número hacía `.replace(",", ".")` a lo bruto,
asumiendo formato USD sin separador de miles (`"205.18"` → ok). Un
monto ARS con separador de miles y coma decimal (`"45.320,00"`) se
rompía: después del `replace` quedaban DOS puntos (`"45.320.00"`),
`float()` explotaba y cae a `None`. Con TRFPO (USD, montos chicos, sin
separador de miles) nunca se disparaba.

Corregido con `_parsear_numero`: identifica cuál separador (`,` o `.`)
es el DECIMAL por cuál aparece más a la derecha del string, y descarta
el otro como separador de miles — soporta tanto `"205.18"` (USD) como
`"45.320,00"` (ARS) sin necesitar saber de antemano en qué moneda está
cada celda.

## Fase 3 — comparación de gap (`comparacion_gap.py`)

A pedido de la usuaria: matchear los códigos de TRFPO y de cada
transportista para comparar la variación. Toma `tarifas_vigentes.xlsx`
(salida de Fase 1) y produce `comparacion_gap.xlsx` — una fila por cada
(código de transportista, rango de pax, período), con su TRFPO
correspondiente y la diferencia (`DIFERENCIA_USD`, `DIFERENCIA_PCT`).

**Diseño más simple de lo que planteaba el `BRIEF.md` original**: el
brief pensaba este cruce ANTES de tener rangos de pax reales leídos de
Tourplan — proponía inferir vehículo/pax por regex de `Description` y
cruzar contra `tabla_bases_vehiculo_pax.csv`. Pero Fase 1 ya trae el
`PAX_DESDE`/`PAX_HASTA` real de cada código directo de la grilla de
RATES — no hace falta re-derivarlo. El único cruce que hace Fase 3 es:

1. Código de transportista → código TRFPO: longest-prefix-match,
   reusando tal cual `mejor_prefijo_trfpo` de `matching_engine.py`
   (Fase 2) — sin re-implementar la lógica de matching en un segundo
   lugar.
2. De las filas de ESE código TRFPO, la que tenga rango de pax
   superpuesto Y máxima superposición de fechas de período con la fila
   del transportista (dos supplier pueden tener sus períodos cargados
   con fechas distintas — ver ejemplo real: TRFPO `01/04/2026-
   31/08/2026` de un solo tramo vs. 6HOUS1 con dos períodos,
   `01/01/2026-31/07/2026` y `01/08/2026-31/08/2026`, para la misma
   ruta).
3. Gap en USD: `TARIFA_USD` de ambos lados ya viene normalizado por
   Fase 1, así que la resta/porcentaje es directa sin reconversión.

Banderas de salida: `SIN_MATCH_TRFPO` (código de transportista sin
ningún TRFPO cuyo prefijo matchee), `COLISION_REVISAR` (más de un
candidato TRFPO — mismo caso que Fase 2), `SIN_TRFPO_PARA_ESE_PAX`
(hay TRFPO pero ningún rango de pax de ese código cubre el pax del
transportista), `SIN_SUPERPOSICION_DE_PERIODO` (se encontró un TRFPO
igual pero sus fechas no se superponen con las del transportista — la
comparación se hace de todos modos con la fila más cercana, marcada
para revisión), `SIN_TARIFA_USD_PARA_COMPARAR` (a alguno de los dos
lados le falta `TARIFA_USD`, ej. por falta de tipo de cambio).

**Probado contra datos reales** (el `tarifas_vigentes.xlsx` que subió
la usuaria, TRFPO+6HOUS1): reveló el problema de paginación de arriba
— con sólo 22 códigos TRFPO discutido, 172/208 filas de 6HOUS1 salían
`SIN_MATCH_TRFPO` que en realidad debían matchear. No es un bug de
`comparacion_gap.py` en sí — depende enteramente de que Fase 1 haya
descubierto TODOS los códigos TRFPO. Repetir la comparación después de
una corrida de Fase 1 con la paginación corregida.

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
