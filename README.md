# generico-vs-especificos

App local (Streamlit) para comparar, en Tourplan NX, el costo cargado en un
supplier **genérico** (TRFPO, GUIAPO, PEAPO y los demás "por asignar") contra
los tarifarios reales de cada proveedor **específico**. Lee las tarifas con
Selenium, las archiva en Google Sheets (un Sheet por genérico) y calcula la
variación en USD y % por código, pax break y período.

Ver `BRIEF.md` (pedido de negocio original) y `DISENO.md` (decisiones de
diseño, hallazgos de validación y pendientes).

## Cómo usarla

1. **Instalar** (una sola vez): Python 3, Git y Google Chrome. Clonar el repo
   en una carpeta con ruta corta.
2. **Abrir la app**: doble clic en `app/run_app.bat` (la primera vez instala
   las dependencias). Se abre en `localhost:8501`. Para traer cambios nuevos
   del repo: `app/actualizar_app.bat`.
3. **⚙️ Configuración** (una vez por PC, se guarda en
   `~/.tourplan-nx-app/config.json`, fuera del repo): usuario y password de
   Tourplan, URL de Tourplan (producción por defecto), la URL del Google Sheet
   de salida de cada genérico (TRFPO, GUIAPO, PEAPO a la vista, el resto en un
   desplegable), el modo headless y el `credentials.json` de Google (OAuth de
   escritorio). La primera corrida abre el navegador para dar permiso a
   Google.
4. **▶ Comparación**:
   - **Modo**: *Solo genérico* (extrae y archiva el genérico), *Solo
     específico* (extrae específicos y los compara contra el genérico
     archivado) o *Completo*.
   - **Genérico y locations** (selección múltiple): el catálogo está en
     `config/genericos.csv`; cada genérico habilita sólo las locations donde
     está cargado.
   - **Específicos**: código o nombre del proveedor, separados por coma.
   - **Rango de fechas** (opcional; sin rango = período vigente hoy).
   - **Actualizar variación**: para usar justo después de modificar valores en
     Tourplan; se elige el servicio y el/los proveedores que cambiaron y sólo
     se vuelven a leer esos.

## Qué guarda

En el Sheet de cada genérico (las pestañas se crean solas):

- `GENERICOS`: tarifas archivadas del genérico, una fila por código × período ×
  pax break.
- `ESPECIFICOS`: lo mismo para los proveedores específicos (también es el
  registro de qué se puede actualizar).
- `COMPARACION`: el gap genérico vs específico, con `FLAGS` para lo que hay que
  revisar. Se calcula al terminar cada proveedor y al final de la corrida.

Las tarifas se guardan código a código mientras se leen; si se aborta o hay un
error no se pierde lo ya leído.

## Estructura

- `app/` — la app: `app.py` (interfaz), `common/` (credenciales, Sheets,
  abortar, Chrome), `run_app.bat`, `actualizar_app.bat`, `requirements.txt`.
- `variacion_generico_especifico.py` — el script que ejecuta la app (Selenium +
  Google Sheets + comparación), parametrizado por variables de entorno.
- `config/genericos.csv` — catálogo de genéricos (location, supplier, nombre,
  service type).
- `config/tabla_bases_vehiculo_pax.csv` — vehículo según rango de pax por
  location/categoría/guía; el script tiene una copia embebida (mantenerlas
  sincronizadas si se edita).
- `BRIEF.md`, `DISENO.md` — documentación.

## Estado

- **TRFPO en BUE**: genérico y comparación validados contra Tourplan real desde
  la app (match por pax break y por vehículo, peajes PEAPO incluidos).
- **Sin validar todavía**: GUIAPO, BOXLPO, bodega, navegación, parking y tip
  (Price Code, service type y criterio de match propios); búsqueda de específico
  por nombre (sólo por código está confirmada).
- **Pendientes**: bandera para genérico en 0 y para superposición parcial de
  períodos; botón para recalcular la comparación sin leer Tourplan; autocompletar
  el proveedor consultando Tourplan; definir el Sheet de GUIAPJ/GUIAPR. Detalle
  en `DISENO.md`.

## Historial

Antes de pasar todo a la app existían scripts sueltos para Google Colab
(`extraccion_tarifas_vigentes.py`, `comparacion_gap.py`, `matching_engine.py`,
`test_matching.py`, `extraccion_vigencias.py` y las muestras de `muestras/`). Se
eliminaron del árbol de trabajo pero siguen en el historial de git, por ejemplo
`git show 1cfc007:extraccion_tarifas_vigentes.py`.
