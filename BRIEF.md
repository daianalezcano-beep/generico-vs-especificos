# Prompt para Claude Code — Herramienta de comparación TRFPO vs tarifarios reales de transportistas

Pegar este brief como primer mensaje al abrir el proyecto en Claude Code. Adjuntar junto con él los 3 CSV de ejemplo (`Product_List_...csv`: uno de TRFPO, dos de transportistas) y, si están disponibles, exports de tarifas reales (TARIFA_ANTES) del Impact Simulator para los mismos proveedores.

---

## Contexto de negocio

Trabajamos en una agencia de turismo que usa **Tourplan NX**. Para componentes genéricos de transporte (vehículo, guía, peajes) cargamos un valor "base" en el sistema bajo el supplier genérico **TRFPO** (Transporte Por Asignar). Ese valor base se decide temporada a temporada según una política comercial (usar el tarifario más barato, el más caro, un promedio, el más usado, etc.) entre los tarifarios reales de cada transportista.

**Objetivo**: construir una herramienta que compare el valor cargado en TRFPO contra los tarifarios reales vigentes de cada transportista, para saber si el costo base está desalineado y cuánto ajuste necesitaría.

Ya construimos una herramienta hermana para un problema relacionado (impacto de cambio de tipo de cambio ARS→USD sobre tarifas). Reusar esa lógica de severidad/reporting donde aplique, pero esta herramienta es distinta: el benchmark no es un % fijo de devaluación, es "la política comercial vigente" comparada contra el grupo de tarifarios reales.

## Problema técnico central: matching de códigos

TRFPO tiene **un código genérico por ruta/servicio** (ej. `EZHT` = traslado Ezeiza-Hotel). Cada transportista tiene, para esa misma ruta, **varios códigos específicos, uno por tipo de vehículo**, con un sufijo propio que **no está estandarizado entre transportistas** (ej. `EZHT19`/`EZHT24`/`EZHT42` en un transportista que numera por capacidad, `EZHTAU` en otro que usa letras). No se puede decodificar el sufijo de forma genérica.

**Solución validada con datos reales** (ver `## Formato de los datos de entrada` abajo): el campo `Description` de cada código específico ya trae el vehículo y el rango de pax en texto explícito, ej.:
- `"TR AEP - EZE Sprinter 19 (5/11 pax)"` → vehículo=Sprinter 19, pax 5 a 11
- `"TR EZE - HT AUTO (1/2 pax)"` → vehículo=Auto, pax 1 a 2

No hace falta interpretar el sufijo del código en absoluto — se parsea el vehículo y el rango de pax directamente del texto de `Description`.

## Algoritmo de matching (ya prototipado y validado)

1. **Matching código específico → código genérico TRFPO**: usar *longest prefix match* — el código genérico TRFPO es siempre el prefijo del código específico del transportista, pero puede haber colisiones entre códigos TRFPO (ej. `AEEZ` es prefijo de `AEEZSI`), así que hay que tomar siempre **el prefijo más largo que matchea**, no el primero que aparezca. Casos de colisión detectados en los datos de ejemplo: `AEEZ`/`AEEZSI`, `AEHT`/`AEHTSI`, `EZHT`/`EZHTSI`, `HTAE`/`HTAESI`, `CT3`/`CT3SIB`, `FAR`/`FARC`, `FTI`/`FTISIB`, `HRD`/`HRD3`/`HRD4`/`HRD5`, `HTI`/`HTISIB`.

2. **Parseo de vehículo y rango de pax desde `Description`** (regex, no desde el código):
   - Rango de pax: patrón `\((\d+)\s*/?\s*(\d*)\s*pax\)` (soporta `(5/11 pax)`, `(1/2pax)`, `(1 pax)`).
   - Vehículo: buscar por keywords conocidos en la descripción (no tomar "la última palabra antes del paréntesis", eso da falsos positivos con palabras como "Japon", "Crucero", "s/guia", "Euro"). Lista de vehículos a reconocer, en este orden de prioridad (más específico primero):
     - `AUTO`
     - `H1` o `VITO` → "H1/VITO"
     - `SPRINTER 15` / `SP 15`
     - `SPRINTER 19` / `SP 19` / `SP19`
     - `MINIBUS 24`
     - `MINIBUS` (sin número)
     - `BUS + VAN` / `BUS + VH` → "BUS + VAN EQUIPAJE"
     - `BUS`

3. **Banderas adicionales a detectar** (por texto Y por prefijo de código, ver nota abajo):
   - `JAPON`: código empieza con `J` **o** descripción contiene "Japon". **Importante**: se detectaron casos reales (`JFAR`, `JFSU`, `JRE`) donde la descripción NO contiene la palabra "Japon" pero el código sí tiene el prefijo `J` de la familia Japón — el prefijo de código es la señal más confiable acá, hay que usar OR de ambas condiciones.
   - `CRUCERO`: descripción contiene "Crucero".
   - `SIN_GUIA`: descripción contiene "s/guia" **o** código empieza con `N`.
   - `CASO_ESPECIAL_SIB`: código contiene "SI"/"SIB" al final y/o descripción contiene "valoriza al 50%". Estos son una regla comercial aparte (tarifa a mitad de precio para un canal específico) — **excluir de la comparación estándar de vehículo/pax**, tratarlos en una categoría separada o compararlos ya ajustados al 50%.
   - `NO_TRANSPORTE`: códigos que no son servicios de transporte real, detectados en los datos de ejemplo: `MINWAT` (agua mineral), `700TRF` (transfer genérico sin desglose), `TRF19/TRF24/TRF42` ("TR Generico", parecen tarifas de respaldo/fallback, no ligadas a una ruta específica) — excluir del matching por vehículo, listar aparte.

4. **Clasificación de cada código TRFPO en categoría de servicio**, usando keywords en `Description` (con el ajuste de prefijo de código para Japón del punto 3):
   - `EXCURSION`: contiene "City", "Dinner Show", "FD " (día de campo), "HD Tigre", "Solo Show", "Hr Dispo", "Hr Espera", "Meeting Point", "Dispo Guia".
   - `TRASLADO`: resto de rutas punto a punto entre aeropuertos/hotel/terminal (default si no matchea ninguna otra categoría).
   - `JAPON`, `CRUCEROS`, `SIN_GUIA`: igual que el punto 3.

## Tabla de bases (input de negocio — vehículo según rango de pax, POR LOCATION)

**Importante**: esta tabla es específica de cada `LOCATION` donde existe TRFPO (cada ubicación tiene su propia flota disponible y por lo tanto su propia relación vehículo↔rango de pax). El CSV adjunto (`tabla_bases_vehiculo_pax.csv`) ya tiene **3 locations cargadas: BUE, FTE, USH**. El diseño de la herramienta debe soportar N locations desde el principio:

- La tabla de bases tiene `LOCATION` como primera columna.
- El motor de matching y clasificación debe indexar por `(LOCATION, CATEGORIA, GUIA, PAX)`, no asumir una sola tabla global.
- Si para alguna location no hay tabla de bases cargada todavía, la herramienta debe **marcarlo explícitamente** (ej. flag `SIN_TABLA_BASES_PARA_LOCATION`) en vez de aplicar por defecto la de otra location — son flotas distintas y no se puede asumir que el mapeo pax→vehículo es el mismo.
- A medida que se sumen tablas de otras locations, se agregan como filas nuevas al mismo CSV (misma estructura, cambia solo `LOCATION`), no como archivos separados.

**Dos particularidades detectadas al cargar FTE y USH que el motor tiene que soportar:**

1. **No todas las locations diferencian Con Guía / Sin Guía.** BUE sí distingue (rangos distintos para algunas categorías); FTE y USH traen un solo rango de pax por vehículo, válido para ambos casos. En el CSV esto se resolvió duplicando la fila para `CON_GUIA` y `SIN_GUIA` con el mismo rango, para que el esquema `(LOCATION, CATEGORIA, GUIA, PAX)` sea consistente en todas las locations sin lógica condicional aparte. Si aparece una location nueva sin distinción de guía, cargarla de la misma forma (duplicar ambas filas).

2. **Puede haber más de un vehículo válido para el mismo rango de pax.** Ejemplos reales: en FTE y USH, "Auto" y "Doble Tracción (DT)" cubren ambos el rango 1‑2 pax; en USH, "Hi Ace/Van 12 pax" y "Sprinter 15 pax" cubren ambos el rango 5‑6/5‑8 pax en Traslado/Excursión. Esto es intencional — la elección real depende de la ruta/terreno, no solo de la cantidad de pax (ej. rutas de montaña o ripio en Ushuaia/Calafate usan doble tracción aunque el pax count sea el mismo que para un Auto convencional). **El motor de comparación NO debe tratar esto como error ni forzar un único vehículo por rango**: cuando haya más de un vehículo candidato para un mismo `(LOCATION, CATEGORIA, GUIA, PAX)`, el grupo de tarifas reales comparables debe incluir las tarifas de **todos** los vehículos candidatos (unión, no exclusión mutua). El parseo de vehículo desde `Description` de cada transportista (ver sección de matching) sigue siendo la fuente de verdad de qué vehículo es cada tarifa real — la tabla de bases solo acota el universo de vehículos aceptables para ese tramo, no obliga a elegir uno.

Contenido actual (BUE — columnas: Location, Categoría, Vehículo, Con Guía — rango pax, Sin Guía — rango pax):

**EXCURSIÓN**

| Vehículo | Con Guía (pax) | Sin Guía (pax) |
|---|---|---|
| Auto | 1 a 2 | 1 a 2 |
| H1/VITO | 3 a 4 | 3 a 5 |
| Sprinter 15 pax | 5 a 11 | 6 a 11 |
| Sprinter 19 pax | 12 a 13 | — |
| Minibus | 14 a 19 | — |
| Bus | 20 a 41 | — |

**TRASLADO**

| Vehículo | Con Guía (pax) | Sin Guía (pax) |
|---|---|---|
| Auto | 1 a 2 | 1 a 2 |
| H1/VITO | 3 a 4 | 3 a 4 |
| Sprinter 19 pax | 5 a 11 | 5 a 11 |
| Minibus | 12 a 15 | — |
| Bus | 16 a 35 | — |
| Bus + VH para equipaje | 36 a 41 | — |

**JAPÓN** (una sola columna de pax, sin distinción con/sin guía)

| Vehículo | Pax |
|---|---|
| Auto | 1 a 2 |
| H1/VITO | 3 |
| Sprinter 19 pax | 4 a 5 |
| Minibus | 6 a 14 |
| Bus | 15 a 38 |

**CRUCEROS (excursión y traslado con equipaje) — EXCURSIÓN**

| Vehículo | Con Guía (pax) | Sin Guía (pax) |
|---|---|---|
| Auto | 1 | 1 |
| H1/VITO | 2 a 3 | 2 a 3 |
| Sprinter 15 pax | 4 a 6 | 4 a 6 |
| Sprinter 19 pax | 7 a 9 | 7 a 9 |
| Minibus | 10 a 13 | 10 a 11 |
| Bus | 14 a 25 | — |
| Bus + Van para equipaje | 26 a 41 | — |

**CRUCEROS — TRASLADO**

| Vehículo | Con Guía (pax) | Sin Guía (pax) |
|---|---|---|
| Auto | 1 | 1 |
| H1/VITO | 2 a 3 | 2 a 3 |
| Sprinter 19 pax | 4 a 9 | 4 a 9 |
| Minibus | 10 a 13 | 10 a 11 |
| Bus | 14 a 25 | — |
| Bus + VH para equipaje | 26 a 41 | — |

*(Las 5 tablas de BUE de arriba —Excursión, Traslado, Japón, Cruceros Excursión, Cruceros Traslado— corresponden solo a esa location. FTE y USH no tienen desglose de Cruceros todavía; si se suma, cargarlo con la misma estructura.)*

**CRUCEROS — TRASLADO**

| Vehículo | Con Guía (pax) | Sin Guía (pax) |
|---|---|---|
| Auto | 1 | 1 |
| H1/VITO | 2 a 3 | 2 a 3 |
| Sprinter 19 pax | 4 a 9 | 4 a 9 |
| Minibus | 10 a 13 | 10 a 11 |
| Bus | 14 a 25 | — |
| Bus + VH para equipaje | 26 a 41 | — |

---

**FTE (El Calafate) — EXCURSIÓN y TRASLADO** (sin distinción Con/Sin Guía, mismo rango para ambos)

| Vehículo | Excursión (pax) | Traslado (pax) |
|---|---|---|
| Auto | 1 a 2 | 1 a 2 |
| Doble Tracción (DT) | 1 a 2 | 1 a 2 |
| H1/VITO | 3 a 4 | 3 a 4 |
| Sprinter 15 pax | 5 a 11 | 5 a 6 |
| Sprinter 19 pax | 12 a 13 | 7 a 11 |
| Minibus | 14 a 19 | 12 a 15 |
| Bus | 20 a 41 | 16 a 41 |

**FTE — JAPÓN**

| Vehículo | Pax |
|---|---|
| Auto | 1 a 2 |
| H1/VITO | 3 |
| Sprinter 19 pax | 4 a 5 |
| Minibus | 6 a 14 |
| Bus | 15 a 38 |

**USH (Ushuaia) — EXCURSIÓN y TRASLADO** (sin distinción Con/Sin Guía, mismo rango para ambos)

| Vehículo | Excursión (pax) | Traslado (pax) |
|---|---|---|
| Auto | 1 a 2 | 1 a 2 |
| Doble Tracción (DT) | 1 a 2 | 1 a 2 |
| H1/VITO | 3 a 4 | 3 a 4 |
| Hi Ace/Van 12 pax | 5 a 8 | 5 a 6 |
| Sprinter 15 pax | 5 a 11 | 5 a 6 |
| Sprinter 19 pax | 12 a 13 | 7 a 11 |
| Minibus | 14 a 19 | 12 a 15 |
| Bus | 20 a 42 | 16 a 41 |

**USH — JAPÓN**

| Vehículo | Pax |
|---|---|
| Auto | 1 a 2 |
| H1/VITO | 3 |
| Sprinter 19 pax | 4 a 5 |
| Minibus | 6 a 14 |
| Bus | 15 a 38 |

*(Notar los rangos superpuestos dentro de una misma location — ej. USH Traslado: Hi Ace/Van 12 pax y Sprinter 15 pax cubren ambos 5‑6 pax; FTE/USH: Auto y Doble Tracción cubren ambos 1‑2 pax. Es intencional, ver regla de "múltiples vehículos por rango" arriba.)*

### Regla de negocio clave sobre el tramo "vehículo + equipaje"

Cuando la tabla separa un tramo "vehículo" de un tramo "vehículo + adicional para equipaje" (porque a esa cantidad de pax con equipaje no entra en el vehículo estándar), y el transportista tiene cargados los dos tramos por separado en TP, **la tarifa del tramo superior ya incluye el costo del vehículo adicional** — no hay que sumar ni descomponer nada, se compara cada tramo tal cual está cargado. Si el transportista **no diferencia** ese tramo (carga un solo código que cubre todo el rango combinado, ej. Bus 16‑41 en una sola tarifa), **no es un error**: marcar como aviso informativo ("proveedor no diferencia tramo con vehículo de equipaje — verificar si la tarifa cargada ya lo contempla") y comparar ese código único contra el tramo combinado equivalente de TRFPO.

## Formato de los datos de entrada

### 1. Catálogo de códigos (Product List export de TP) — ya disponible, formato validado

CSV con columnas: `Loc, Serv, Supplier, SupplierName, Code, Description, Comment, Used, Deleted`. Un archivo por supplier (TRFPO y cada transportista). Ejemplos reales:

```
BUE,TR,TRFPO,Transporte Por Asignar,EZHT,Vh x Asig EZE - HT,MT: htl Palermo = EZPA (solo si el prov tiene 2 tarifas),Y,N,
BUE,TR,1TEP01,Turismo El Puente S.A. (Transportista),EZHT19,TR EZE - HT Sprinter 19 (5/11 pax),,Y,N,
BUE,TR,1TEP01,Turismo El Puente S.A. (Transportista),EZHT24,TR EZE - HT Minibus 24 (12/15 pax),,Y,N,
BUE,TR,1TEP01,Turismo El Puente S.A. (Transportista),EZHT42,TR EZE - HT Bus (16/41 pax),,Y,N,
BUE,TR,6HOUS1,Houseman Cars (Transportista),EZHTAU,TR EZE - HT AUTO (1/2 pax),,Y,N,
```

### 2. Tarifas vigentes (falta resolver — ver siguiente sección)

## Fase 1 (prioritaria): script de extracción rápida de tarifas vigentes

Hoy la única forma de obtener tarifas reales es correr el "Impact Simulator" de tipo de cambio (que ya tenemos scriptado/documentado en un proyecto hermano) y quedarse con la columna `TARIFA_ANTES` — pero ese script es lento porque calcula de más: simula un cambio de tipo de cambio, recalcula `TARIFA_DESPUES`, `DIFERENCIA_NOMINAL`, `DIFERENCIA_PCT` para cada rango de pax, cuando lo único que necesitamos acá es la tarifa vigente.

**Tarea para Claude Code**: partir del script existente del Impact Simulator (se adjuntará su código/lógica) y crear una **versión reducida que solo extraiga**:
- `SUPPLIER`, `PRODUCT_CODE`, `RANGO_PAX` (desde/hasta), `TARIFA_VIGENTE`, `PERIODO`, `LOCATION`, `TIMESTAMP`

eliminando cualquier paso de simulación de tipo de cambio, cálculo de `DESPUES`/`DIFERENCIA`, o doble pasada innecesaria. El objetivo es reducir drásticamente el tiempo de ejecución corriendo esto para múltiples proveedores (TRFPO + N transportistas) en una sola pasada o en paralelo si es posible.

Primero investigar/perfilar por qué el script actual es lento (¿automatiza la UI de TP paso a paso? ¿hace requests repetidos por código? ¿corre en single-thread?) antes de optimizar a ciegas.

## Fase 2: motor de matching (código + vehículo + pax) — diseño ya validado, implementar

Implementar el algoritmo de matching y clasificación descrito arriba. Salida: una **tabla de mapeo persistente** (no recalcular de cero cada corrida):

| CODIGO_TRFPO | CATEGORIA | GUIA | CODIGO_REAL | TRANSPORTISTA | VEHICULO | PAX_DESDE | PAX_HASTA | FLAGS |
|---|---|---|---|---|---|---|---|---|

Con columna `FLAGS` para: `SIN_MATCH`, `CASO_ESPECIAL_SIB`, `NO_TRANSPORTE`, `COLISION_REVISAR`, `SIN_COBERTURA_VEHICULO` (ej. ninguna tarifa real para el tramo H1/VITO en esa ruta), `TRAMO_NO_DIFERENCIADO` (proveedor no separó vehículo+equipaje).

## Fase 3: motor de comparación (gap TRFPO vs tarifario real)

Por cada código TRFPO + tramo de pax:
1. Determinar vehículo aplicable (tabla de bases, según categoría/guía del código).
2. Ubicar, entre las tarifas reales matcheadas para ese código+vehículo, el grupo comparable (puede ser de 1 a N transportistas).
3. Calcular tarifa "esperada" según la política comercial vigente esa temporada (parámetro configurable: mínimo, máximo, promedio, más usado — este último requiere un dato de volumen/uso que puede no estar disponible, dejar como input manual si hace falta).
4. Comparar TRFPO vigente vs esa tarifa esperada: gap nominal, %, severidad:
   - **CRÍTICO**: TRFPO fuera del rango [mínimo, máximo] del grupo real.
   - **ALTO**: TRFPO se aparta más de X% (parámetro) del valor esperado según la política declarada.
   - **OK**: dentro de tolerancia.

## Fase 4: salida

Mismo esquema de pestañas Excel que la herramienta de tipo de cambio (Resumen Ejecutivo, Anomalías Prioritarias, Resumen por Transportista/Vehículo, Base Unificada, Sin Mapear/Requiere Revisión), fuente Arial, coloreado por severidad, autofiltros y freeze panes.

## Stack sugerido

Python + pandas para el procesamiento, openpyxl para la salida Excel (reusar patrones del proyecto de tipo de cambio: recalc, formato, tablas). La tabla de bases y los umbrales de severidad deben ser archivos de configuración editables, no valores hardcodeados en el código.

## Adjuntos a incluir en la primera sesión de Claude Code

1. Los 3 CSV de ejemplo (`Product_List_...csv`: TRFPO + 2 transportistas).
2. El script/lógica actual del Impact Simulator (para la Fase 1 de optimización).
3. La tabla de bases de este documento, como CSV aparte (`tabla_bases_vehiculo_pax.csv`) para que Claude Code la cargue como dato, no como texto a re-parsear. Ya cubre **BUE, FTE y USH** — si existen más locations con TRFPO, agregarlas al mismo CSV antes de correr la herramienta sobre esas ubicaciones (ver sección "Tabla de bases" arriba).
