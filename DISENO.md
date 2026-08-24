# Diseño: genérico (TRFPO/PEAPO/GUIAPO) vs tarifarios reales de específicos

Ver `BRIEF.md` para el pedido de negocio completo. Este documento registra
las decisiones de diseño tomadas durante la construcción (algunas
ajustan/precisan lo que dice `BRIEF.md` a partir de datos reales).

## Estado actual (resumen — ver secciones más abajo para el detalle)

**Fase 2 (matching, `matching_engine.py`)**: terminada, validada contra
los 3 Product List reales de `muestras/` (`python test_matching.py`).
Sin pendientes propios — ver "Motor de matching" más abajo por los
hallazgos de esa validación.

**Fase 1 + Fase 3 en una sola corrida (`extraccion_tarifas_vigentes.py`)**:
extrae tarifas vigentes de Tourplan real Y calcula la comparación de
gap al final, en la misma ejecución. Validado con varias corridas
reales contra Tourplan Test — la más reciente trajo 116 códigos TRFPO +
52 de 6HOUS1 en BUE, con 204/208 filas de la comparación sin ninguna
bandera de revisión. En el camino se encontraron y corrigieron bugs
reales (Chrome no preinstalado en Colab, columna Code confundida con
Location, RATES mostrando una lista de períodos en vez de la grilla
directamente, moneda en la lista de períodos y no en la grilla
abierta, formato numérico US/UK y no argentino, catch-all de pax 9999,
período de TRFPO usado en cada comparación no visible) — el detalle de
cada uno está en su propia sección más abajo.

**`comparacion_gap.py`**: la misma Fase 3, como script standalone
(pandas) para re-correr sólo la comparación sin volver a scrapear
Tourplan — misma lógica que la versión embebida, ambas dan resultados
idénticos contra los mismos datos reales.

**`MODO` (`SOLO_GENERICO`/`SOLO_TRANSPORTISTA`/`COMPLETO`)**: TRFPO ya
no hace falta re-extraerlo en cada corrida — se guarda en
`tarifas_trfpo_<LOCATION>.xlsx` y se reusa. Ver "Resuelto: desacoplar
la extracción de TRFPO...".

**`DIFERENCIA_PCT` con signo %**: visible en el Excel sin cambiar el
valor guardado. Ver esa sección.

**Generalización a PEAPO/GUIAPO (y cualquier otro genérico futuro)**:
`COMPARACIONES` ya no está atado a TRFPO — es una lista de relaciones
genérico/específico independientes, cada una con su propio genérico,
service type, específicos y Price Code. Ver "Generalización: soporte
para múltiples relaciones genérico/específico (PEAPO, GUIAPO, ...)".

**Pendiente**: Fase 4 (Excel final con el gap resaltado/formateado) sin
empezar. Backlog restante: generar una fila por cada período de TRFPO
cuando hay más de uno superpuesto en vez de sólo el de mayor
superposición (ver "Visibilidad de qué período se comparó"); scroll de
`listar_codigos_supplier` sigue sin ser 100% confiable de corrida a
corrida (ver "Scroll virtual"); PEAPO/GUIAPO están soportados en el
código pero sin validar aún contra Tourplan real (sólo TRFPO fue
validado con corridas reales).

## Alcance

Fase 1 (extracción de tarifas vigentes) + Fase 2 (motor de matching) +
Fase 3 (comparación de gap), con conversión de moneda incluida en las
tres. Fase 4 (Excel de salida final con formato/resaltado) queda
pendiente — el archivo de config `tabla_bases_vehiculo_pax.csv` está
armado por si hace falta ahí (Fase 3 terminó sin necesitarlo, ver esa
sección).

## Corrección a BRIEF.md: los sufijos de código SÍ están estandarizados

`BRIEF.md` (sección "Problema técnico central: matching de códigos")
dice que el sufijo de cada código específico de transportista "no está
estandarizado entre transportistas" (ej. `EZHT19`/`EZHT24`/`EZHT42` en
uno, `EZHTAU` en otro). Aclaración de la usuaria: eso describe mal la
causa — los sufijos SÍ están estandarizados (mismo significado siempre:
"19"=Sprinter 19, "24"=Minibus 24, "42"=Bus, "AU"=Auto, etc.). Lo que
varía es que no todos los transportistas tienen la misma flota, así que
cada uno usa sólo el subconjunto de sufijos de los vehículos que
efectivamente tiene — no es que el mismo sufijo signifique otra cosa
según el transportista.

No cambia nada del código: el motor de matching (`matching_engine.py`,
`mejor_prefijo_trfpo`) nunca decodificó el sufijo para el matching en sí
(matchea por prefijo de código, sin mirar qué significa el sufijo) ni
para el vehículo (lo parsea de `Description`, no del código) — sigue
siendo la estrategia correcta con esta aclaración, no hacía falta un
diccionario de sufijos para nada de lo ya construido. Sí es útil como
contexto para entender los `SIN_MATCH`/huecos de cobertura que aparecen
en los datos reales: si un transportista no tiene, por ejemplo, un Bus
en su flota, directamente no va a existir ningún código suyo con sufijo
"42" para esa ruta — no es un error de carga ni de matching.

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
sin PCM, sin USED IN, sin subcode — sólo la tarifa vigente por rango de
pax de cada código, para TRFPO y cada transportista, dentro del rango
de fechas configurado (`PERIODO_ANALISIS_DESDE/HASTA` — ver sección
"Períodos y Price Code").

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

**Validado con varias corridas reales contra Tourplan Test** — mismo
patrón que siguieron los scripts hermanos (se depuraron corrida a
corrida, ver sus HISTORIAL/README), acá también: cada corrida real
encontró algo para ajustar, documentado en su propia sección más abajo
(Chrome en Colab, columna Code vs. Location, lista de períodos, scroll
virtual, moneda, formato numérico). `listar_codigos_supplier` fue la
parte con menos precedente (no viene adaptada de ningún script
hermano) y la que más ajustes necesitó — ver "Scroll virtual" más
abajo por el estado actual de su heurística de columnas y de scroll.

## Scroll virtual en listar_codigos_supplier (confirmado por captura real)

Corriendo `comparacion_gap.py` contra datos reales: TRFPO devolvió sólo
**22 códigos** en BUE, y de los 52 códigos de 6HOUS1, **172 de 208
filas** no encontraron ningún prefijo TRFPO — pero mirando los códigos
uno por uno, la mayoría SON rutas de transporte normales (`EZHTAU`,
`HTAEAU`, `HRDAU`, `FARAU`, etc.) que en las muestras
(`muestras/Product_List_TRFPO.csv`) sí tienen su TRFPO correspondiente
(`EZHT`, `HTAE`, `HRD`, `FAR`...). 22 es un número sospechosamente
redondo para ser el catálogo completo de TRFPO en BUE.

**Confirmado con una captura real de la pantalla RESULTS de Product
Search**: NO hay ningún control de paginación (ni "siguiente" ni
números de página) — es una lista larga con scrollbar. La cantidad de
filas visibles en el viewport de la captura (~20) coincide con los 22
códigos que se estaban leyendo siempre: la grilla usa scroll VIRTUAL
(sólo renderiza las filas visibles en el DOM), así que
`document.querySelectorAll('table tbody tr')` sólo veía lo que entraba
en pantalla, sin necesidad de ningún botón — hacía falta scrollear.

Se reemplazó el enfoque de "página siguiente" (basado en un botón que
no existe) por `_hacer_scroll_resultados`: busca el ancestro con scroll
real más cercano a la tabla y le corre `scrollTop` un `clientHeight` por
vez, re-leyendo después de cada scroll y acumulando por código hasta 3
scrolls seguidos sin códigos nuevos. Headers de la tabla confirmados
por la misma captura: `LOCATION, SERVICE, SUPPLIER, SUPPLIER NAME,
RATE PERIOD, CODE, DESCRIPTION, COMMENT` — coincide con la detección de
columna "CODE" ya implementada (match exacto, sin necesitar el
fallback heurístico). **Confirmado en la corrida siguiente**: TRFPO
pasó de 22 a 116 códigos en BUE.

**El scroll virtual sigue sin ser 100% confiable de corrida a
corrida** — hallazgo posterior: en otra corrida, TRFPO no descubrió
`HTHT`/`HTMP`/`NEZH` (confirmados como códigos TRFPO reales en
`muestras/Product_List_TRFPO.csv`), a pesar de que una corrida anterior
sí había llegado a códigos parecidos. No es un problema del matching
(`"HTHTAU".startswith("HTHT")` es trivialmente correcto) — es que esos
códigos directamente no estaban en la lista que devolvió
`listar_codigos_supplier` esa vez. Hipótesis: el umbral de "3 scrolls
seguidos sin nada nuevo" cortaba la búsqueda antes de tiempo si el
scroll virtual tardaba más de lo esperado en renderizar filas nuevas
(dependiente de latencia/timing, no determinístico).

Mitigado (sin poder confirmar que sea 100% suficiente sin otra corrida
real): el umbral subió de 3 a 5 scrolls seguidos sin novedad, el sleep
entre scrolls subió de 1s a 1.5s, y se agregó una pasada final
defensiva que salta directo a `scrollHeight` (el fondo real del
contenedor, no `+clientHeight` incremental) y relee una vez más
después de que el loop principal termina — por si el loop incremental
igual cortó antes de llegar al final. Si esto se sigue repitiendo,
convendría verificar con la usuaria si hay una forma más determinística
de saber "llegué al final" (ej. un contador de resultados visible en
algún lado de la pantalla de Product Search) en vez de inferirlo por
scrolls sin novedad.

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

## Parseo de números (formato US/UK confirmado, no argentino)

Confirmado con datos reales: los códigos de 6HOUS1 (ARS) traían
`TARIFA_VIGENTE=None` en el rango "1-2 AD" (donde está el valor real
que la usuaria SÍ ve en pantalla) y `0` en "3-9999" (catch-all, 0
esperado) — en el 100% de sus ~50 códigos, siempre el mismo patrón.
Causa original: el parseo de número hacía `.replace(",", ".")` a lo
bruto. Con TRFPO (USD, `"205.18"`, sin separador de miles) no se
notaba. Primer intento de arreglo: asumir que un monto con AMBOS
separadores usa el formato argentino (punto de miles + coma decimal,
ej. `"45.320,00"`) y detectar cuál es cuál por la posición — pero
**confirmado con un valor real de 6HOUS1** ("85,000", sin punto en
absoluto — sólo puede ser "ochenta y cinco mil", con la coma como
separador de MILES, no decimal; si fuera decimal sería "85 pesos", sin
sentido para un transfer), quedó claro que Tourplan usa formato US/UK
para TODAS las monedas, no formato argentino para ARS — la hipótesis
del punto anterior estaba mal.

`_parsear_numero` quedó simplificado: siempre quita las comas
(separador de miles) y deja el punto como decimal, sin intentar
adivinar por posición. Válido tanto para `"205.18"` (USD) como para
`"85,000"`/`"85,000.00"` (ARS). Si en algún momento aparece un valor
real en formato argentino genuino (punto de miles + coma decimal), esta
regla lo rompería — no hay evidencia de que eso pase, pero si aparece
un TARIFA_VIGENTE sospechosamente grande o pequeño en una corrida real,
revisar acá primero.

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
la usuaria, TRFPO+6HOUS1): la primera vez reveló el problema de scroll
virtual de arriba — con sólo 22 códigos TRFPO descubiertos, 172/208
filas de 6HOUS1 salían `SIN_MATCH_TRFPO` que en realidad debían
matchear (no era un bug de esta lógica en sí, dependía de que Fase 1
descubriera TODOS los códigos TRFPO). Repetida la comparación con el
scroll corregido (116 códigos TRFPO): 204/208 filas sin ninguna
bandera, sólo 4 `COLISION_REVISAR`.

### Embebida en extraccion_tarifas_vigentes.py (una sola ejecución)

A pedido de la usuaria (le resultaba confuso tener que correr dos
scripts en dos lugares distintos): la misma lógica de arriba se portó
a Python puro (sin pandas — `_mejor_prefijo_trfpo`,
`_dias_superposicion`, `construir_comparacion_gap` en
`extraccion_tarifas_vigentes.py`) y se llama automáticamente al final
de `main()`, sobre `filas_salida` ya en memoria (sin necesidad de
releer el Excel). Una sola corrida de Colab ahora genera los dos
archivos: `tarifas_vigentes.xlsx` y `comparacion_gap.xlsx`.

Se aceptó duplicar estas ~4 funciones chicas entre
`extraccion_tarifas_vigentes.py` y `comparacion_gap.py` (en vez de que
uno importe al otro) para que el primero siga siendo un solo archivo
autocontenido pegable en una celda de Colab — mismo criterio que ya
tenían los scripts hermanos. `comparacion_gap.py` standalone se
mantiene aparte para poder re-correr sólo la comparación sin
re-scrapear Tourplan. Si se cambia la lógica de matching/gap, replicar
el cambio en ambos archivos — probado que dan resultados idénticos
contra los mismos datos reales (208 filas, mismo desglose de banderas).

## Visibilidad de qué período se comparó (pregunta real de la usuaria)

Pregunta: si alguna de las partes (TRFPO o el transportista) tiene más
de un período cargado dentro del mismo rango de pax, ¿cómo se
identifica en la salida?

Antes de este cambio, NO se identificaba: `construir_comparacion_gap`
siempre elegía el período de TRFPO con mayor superposición de fechas
contra el período del transportista y descartaba el resto EN SILENCIO
— la salida sólo mostraba `PERIODO_DESDE`/`PERIODO_HASTA`, que en
realidad eran las fechas del transportista, sin ninguna columna que
mostrara qué período de TRFPO se había usado. Si TRFPO cambiaba de
tarifa a mitad del período del transportista, el segundo período de
TRFPO quedaba invisible en la comparación.

Corregido: se agregan `PERIODO_TRFPO_DESDE`/`PERIODO_TRFPO_HASTA` como
columnas separadas de `PERIODO_TRANSPORTISTA_DESDE`/
`PERIODO_TRANSPORTISTA_HASTA` (renombradas para dejar de ser
ambiguas), y una bandera `MULTIPLES_PERIODOS_TRFPO` cuando efectivamente
había más de un período de TRFPO candidato para ese rango de pax — para
que quede visible que existe otro período de TRFPO a revisar (en
`tarifas_vigentes.xlsx`) en vez de asumir que el elegido por mayor
superposición es automáticamente el correcto.

Nota: en los datos reales vistos hasta ahora, TRFPO siempre tiene UN
solo período por código (`01/04/2026-31/08/2026`) mientras que 6HOUS1
tiene DOS (`01/01-31/07` y `01/08-31/08`) — ese caso YA estaba bien
resuelto antes de este cambio (cada período del transportista genera
su propia fila, comparada contra el único TRFPO). El caso que faltaba
cubrir es el inverso (TRFPO con más de un período) — no confirmado
todavía con datos reales, pero ahora al menos queda visible si ocurre.

**Pendiente sin resolver, a evaluar si aparece en datos reales**: hoy
sigue eligiéndose UN solo período de TRFPO (el de mayor superposición),
no se generan filas separadas por cada período de TRFPO que se
superponga parcialmente. Si en una corrida real aparece
`MULTIPLES_PERIODOS_TRFPO` con superposiciones parciales genuinas (no
sólo un período con 0 días de superposición que quedó ahí por
descarte), reconsiderar si conviene generar una fila por cada período
de TRFPO en vez de una sola con el de mayor superposición.

## DIFERENCIA_PCT con signo % visible

`DIFERENCIA_PCT` ya estaba en escala porcentual (ej. `-44.76` significa
`-44.76%`, no `-0.4476`) — a pedido de la usuaria, ahora se ve como tal
en el Excel. No se usa el formato "Percentage" nativo de Excel (`0%`)
porque ESE formato multiplica el valor por 100 al mostrarlo, y
`DIFERENCIA_PCT` ya viene multiplicado — usarlo mostraría `-4476.00%`.
Se usa en cambio `0.00"%"` (el `%` entre comillas = texto literal para
Excel, no el operador de porcentaje) para agregar el signo sin tocar el
valor. Aplicado en ambos lugares que escriben `comparacion_gap.xlsx`
(`extraccion_tarifas_vigentes.py` embebido, con `openpyxl` directo; y
`comparacion_gap.py` standalone, reabriendo con `openpyxl` después de
`pandas.to_excel` porque pandas no permite formato por celda al
escribir). Probado contra datos reales: el valor guardado no cambia
(`-44.77`), sólo cambia cómo se ve (`-44.77%`).

## Limpieza de salida: descartar catch-all 9999 y redondear a 2 decimales

A pedido de la usuaria, sobre datos reales (948 filas en
`tarifas_vigentes.xlsx`, 220 con `PAX_HASTA=9999`; 208 filas en
`comparacion_gap.xlsx`, 104 con ese mismo catch-all):

1. **Se descartan las filas con `PAX_HASTA=9999`** directamente en
   `_extraer_filas_ad` (Fase 1) — ya no llegan ni a `tarifas_vigentes.xlsx`
   ni, por lo tanto, a la comparación. No es sólo estético: comparar el
   catch-all del transportista (siempre 0, ej. un Auto no aplica más
   allá de 2 pax) contra el catch-all real de TRFPO producía filas
   `DIFERENCIA_PCT=-100%` sin ningún sentido de negocio (visto en los
   datos reales: 104 de las 208 filas de `comparacion_gap.xlsx` eran
   esto). `comparacion_gap.py` standalone también filtra esto al cargar
   el Excel (defensivo, por si se le pasa un `tarifas_vigentes.xlsx` de
   una corrida vieja que todavía no aplicaba este filtro en origen).
2. **Redondeo a 2 decimales** en todos los montos: `_extraer_filas_ad`
   redondea la tarifa apenas se parsea, `convertir_a_usd` redondea el
   resultado de la división (evita el arrastre de decimales tipo
   `113.3333333333333` de una conversión ARS→USD), y `DIFERENCIA_USD`
   se redondea al calcularse (`DIFERENCIA_PCT` ya se redondeaba desde
   antes). Aplicado en `extraccion_tarifas_vigentes.py` Y en
   `comparacion_gap.py` (con un helper `_r2` ahí, ya que ese archivo
   lee montos ya calculados desde el Excel en vez de calcularlos).

Probado contra los datos reales de la usuaria: 948→728 filas en
tarifas vigentes, 208→104 en la comparación, decimales limpios (ej.
`93.3333333333333` → `93.33`).

## Resuelto: desacoplar la extracción de TRFPO de la de cada transportista

Antes, `extraccion_tarifas_vigentes.py` re-extraía TRFPO completo en
CADA corrida, aunque sólo hubiera cambiado a qué transportista se lo
compara. TRFPO es la base compartida entre todas las comparaciones de
una misma location — y según la usuaria, en la práctica se actualiza
sólo 1 o 2 veces al año (cuando se hace un análisis y revalorización
general; después queda fijo como costo base). Re-extraer todo su
catálogo cada vez que se agrega un transportista nuevo (la usuaria
tiene 6-7 para comparar) era trabajo repetido e innecesario.

Implementada la opción 1 del backlog anterior (`MODO` por corrida,
mismo patrón que `MODO = "LEER"/"APLICAR"/"COMPLETO"` de
`tourplan_valorizacion_pkg_v3.py`):

- `MODO = "COMPLETO"` (default): extrae genérico + transportistas,
  como antes.
- `MODO = "SOLO_GENERICO"`: extrae sólo TRFPO. `guardar_cache_trfpo`
  lo guarda en `tarifas_trfpo_<LOCATION>.xlsx` (mismo esquema que
  `tarifas_vigentes.xlsx`, filtrado a `ES_GENERICO=True`). No calcula
  comparación esta corrida (no hay transportista todavía).
- `MODO = "SOLO_TRANSPORTISTA"`: extrae sólo los `"transportistas"` de
  `COMPARACIONES`. `cargar_cache_trfpo` lee
  `tarifas_trfpo_<LOCATION>.xlsx` de una corrida `SOLO_GENERICO`/
  `COMPLETO` anterior (falla con `FileNotFoundError` y mensaje claro si
  no existe) y arma la comparación con eso + lo recién extraído — sin
  volver a tocar TRFPO en Tourplan.

`guardar_cache_trfpo` se llama siempre que la corrida haya incluido
genérico (`COMPLETO` o `SOLO_GENERICO`), pisando el cache anterior de
esa location — así cualquier corrida `COMPLETO` también deja un cache
fresco disponible para una `SOLO_TRANSPORTISTA` futura, sin tener que
acordarse de correr `SOLO_GENERICO` a mano la primera vez.

Validado en aislamiento (round-trip de `guardar_cache_trfpo` +
`cargar_cache_trfpo` contra un Excel real vía openpyxl, sin poder
correr Selenium en este entorno): el booleano `ES_GENERICO` sobrevive
el guardado/lectura como `bool` de Python, no como string — confirmado
antes de dar el cambio por bueno, porque `construir_comparacion_gap`
filtra con `if f["ES_GENERICO"]:` y un string `"True"`/`"FALSE"` mal
tipado hubiera roto ese filtro en silencio.

Se descartaron las otras 2 opciones del backlog por ahora (acumulación
incremental automática: más piezas móviles y riesgo de comparar contra
datos viejos sin darse cuenta; listar transportistas juntos: no
resuelve agregar uno nuevo más adelante sin re-tocar TRFPO).

**Caveat real de Colab, encontrado al pensar el caso de uso real de la
usuaria** ("corro SOLO_GENERICO hoy, SOLO_TRANSPORTISTA la semana que
viene, porque sé que TRFPO no cambió"): el disco de una sesión de Colab
NO persiste entre runtimes distintos — `tarifas_trfpo_<LOCATION>.xlsx`
escrito hoy desaparece si la sesión de Colab de la semana que viene es
una nueva (lo normal, dado que TRFPO se mantiene fijo bastante tiempo
y las corridas van a estar espaciadas). Se agregó `CACHE_DIR` (default
`"."`) para poder apuntar el cache a Google Drive
(`/content/drive/MyDrive/...`) en vez de al disco efímero de la sesión.
El mensaje de error de `cargar_cache_trfpo` cuando no encuentra el
archivo menciona esto explícitamente, para que no se lea como "no
corriste SOLO_GENERICO" cuando en realidad sí se corrió, sólo que en
otra sesión de Colab sin Drive montado.

**Montaje de Drive automático**: a pedido de la usuaria,
`_montar_drive_si_corresponde` llama `drive.mount('/content/drive')`
directamente al principio de `main()` si `CACHE_DIR` empieza con
`"/content/drive"` y el módulo `google.colab` está disponible (es
decir, si esto corre en Colab) — no hace falta que la usuaria agregue
un `drive.mount(...)` manual en otra celda. `drive.mount` es
idempotente (no rompe si ya estaba montado), así que se puede llamar
en cada corrida sin chequeo previo. Límite real que no se puede
programar: la primera vez que Drive se usa desde un navegador/cuenta
nueva, Colab igual muestra un popup de autorización de Google que
necesita un click humano — es un paso de seguridad de Google, no algo
que este script controle.

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

## Generalización: soporte para múltiples relaciones genérico/específico (PEAPO, GUIAPO, ...)

Pedido de la usuaria: que Fase 1 + Fase 3 funcionen no sólo para TRFPO
vs. transportistas, sino también para PEAPO (service type PJ) y GUIAPO
(service type GU), y que quede abierta la puerta a cualquier otra
relación genérico/específico que surja más adelante, sin tener que
volver a tocar código cada vez.

**Por qué no hizo falta cambiar el algoritmo**: el matching
(`_mejor_prefijo_generico` en la versión embebida, renombrado desde
`_mejor_prefijo_trfpo`; `mejor_prefijo_trfpo` de `matching_engine.py`
en el standalone) es puro longest-prefix-match sobre strings — nunca
dependió de que el genérico fuera TRFPO específicamente. Lo único que
había que generalizar era la configuración y el naming, más un caso de
diseño nuevo: qué pasa cuando dos genéricos comparten la misma
`LOCATION`.

**`COMPARACIONES` como lista de relaciones independientes**: cada
elemento de `COMPARACIONES` ya no asume TRFPO — trae su propio
`generico`, `service_type`, `location`, `especificos` (lista de
códigos de proveedor específico) y `price_code`. Agregar PEAPO o
GUIAPO es agregar un diccionario más a la lista, sin tocar el resto del
script. Se dejaron ejemplos comentados de PEAPO (`service_type: "PJ"`)
y GUIAPO (`service_type: "GU"`) directamente en el archivo.

**Compatibilidad hacia atrás en la config**: la clave vieja
`"transportistas"` sigue funcionando como alias de `"especificos"`
(`comp.get("especificos", comp.get("transportistas", []))`), y el
valor viejo de `MODO`, `"SOLO_TRANSPORTISTA"`, se sigue aceptando como
alias de `"SOLO_ESPECIFICOS"`. Esto evita romper una `COMPARACIONES`/
`MODO` que la usuaria ya haya dejado editada de una corrida anterior.
Los nombres de columnas y de banderas en los Excel de salida, en
cambio, se renombraron limpio (sin alias) — son artefactos generados
en cada corrida, no configuración escrita a mano, así que no hay nada
que se rompa por el rename: `CODIGO_TRFPO`→`CODIGO_GENERICO`,
`SUPPLIER_TRANSPORTISTA`→`SUPPLIER_ESPECIFICO`,
`CODIGO_TRANSPORTISTA`→`CODIGO_ESPECIFICO`, `TARIFA_TRFPO_USD`→
`TARIFA_GENERICO_USD`, `TARIFA_TRANSPORTISTA_USD`→
`TARIFA_ESPECIFICO_USD`, y las banderas `SIN_MATCH_TRFPO`→
`SIN_MATCH_GENERICO`, `SIN_TRFPO_PARA_ESE_PAX`→
`SIN_GENERICO_PARA_ESE_PAX`, `MULTIPLES_PERIODOS_TRFPO`→
`MULTIPLES_PERIODOS_GENERICO`.

**Price Code por relación**: no está confirmado que PEAPO/GUIAPO usen
la misma convención de Price Code "TR" que TRFPO, así que cada entrada
de `COMPARACIONES` tiene su propio `"price_code"`; si no se especifica,
se usa el default global `PRICE_CODE_DEFAULT` (sigue siendo `"TR"`).

**Caché por genérico, no sólo por location**: el archivo de caché de
`MODO=SOLO_GENERICO` pasó de `tarifas_trfpo_<LOCATION>.xlsx` a
`tarifas_generico_<GENERICO>_<LOCATION>.xlsx`, agrupando por
`(SUPPLIER, LOCATION)` en vez de sólo `LOCATION` — necesario porque
TRFPO y PEAPO/GUIAPO pueden compartir la misma `LOCATION` (ej. BUE) y
antes se habrían pisado entre sí en el mismo archivo.

**Bug propio encontrado y corregido antes de terminar (no reportado
por la usuaria)**: el primer intento de generalizar
`construir_comparacion_gap` calculaba una variable `clave = (LOCATION,
SUPPLIER si es genérico)` pero después agrupaba sólo por `LOCATION` al
armar los candidatos — es decir, si TRFPO y PEAPO compartieran alguna
vez la misma `LOCATION`, sus filas se habrían mezclado en el matching
sin ningún aviso, exactamente el bug que se estaba tratando de evitar.
Se corrigió agregando una columna nueva `GENERICO` a
`COLS_TARIFAS`/`tarifas_vigentes.xlsx`: autorreferencial en las filas
del genérico (`GENERICO == SUPPLIER`) y con el código del genérico de
la relación en las filas de cada específico. Con esa columna,
`construir_comparacion_gap` (versión embebida) y `construir_comparacion`
(`comparacion_gap.py` standalone) filtran los candidatos genéricos de
cada fila específica por `GENERICO` antes de aplicar el
longest-prefix-match, en vez de agrupar sólo por `LOCATION`. Si el
Excel de entrada es de una corrida vieja y no trae la columna
`GENERICO` (caso de `comparacion_gap.py` leyendo un
`tarifas_vigentes.xlsx` anterior a este cambio), ambas versiones caen
de nuevo al agrupado por `LOCATION` únicamente, para no romper
compatibilidad con archivos ya generados.

**Qué se dejó explícitamente sin generalizar**: `matching_engine.py`
(Fase 2) — parsea vehículo, rango de pax, banderas JAPON/CRUCERO/
CASO_ESPECIAL_SIB y categoría de servicio desde el `Description` de
cada código, lógica intrínsecamente del dominio de transporte. Sólo lo
usa `test_matching.py` para validar offline contra los CSV de
`muestras/` — no forma parte del pipeline en vivo de Fase 1 + Fase 3
que sí necesita soportar PEAPO/GUIAPO. Generalizarlo habría sido
trabajo no pedido y sin caso de uso real todavía.

**Sin validar aún contra Tourplan real**: a diferencia de TRFPO
(validado con varias corridas reales, ver "Estado actual"), el soporte
para PEAPO y GUIAPO es sólo a nivel de código — no se corrió todavía
contra Tourplan Test con datos reales de esos dos genéricos. Antes de
usarlo en producción conviene una corrida con `MODO=SOLO_GENERICO`
sobre PEAPO/GUIAPO para confirmar que el Price Code `"TR"` (o el que
corresponda) y el resto de los supuestos (formato de código,
service type) se sostienen igual que con TRFPO.

**Bug real encontrado en la primera corrida post-generalización**:
la usuaria corrió `MODO=SOLO_GENERICO` y el script logueó en Tourplan
y recién ahí explotó con `KeyError: 'generico'` dentro de
`descubrir_cola` — una entrada de `COMPARACIONES` (agregada/editada a
mano, probablemente al descomentar el ejemplo de PEAPO/GUIAPO) no
tenía la clave `"generico"`. El problema no es sólo el typo en sí,
sino que el script ya había gastado una sesión de las limitadas de
licencia de Tourplan antes de fallar por un error de config. Se agregó
`_validar_comparaciones(COMPARACIONES)`, llamada al principio de
`main()` (antes de `_montar_drive_si_corresponde`/`crear_driver`/
`login`), que chequea que cada entrada tenga `"location"` y
`"generico"` no vacíos y, si falta alguno, lo reporta con un
`ValueError` claro (índice de la entrada + el dict completo) sin
llegar a abrir Chrome.
