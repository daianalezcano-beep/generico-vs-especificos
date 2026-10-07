"""Genérico vs Específicos - app local (Streamlit).

Un único script (variacion_generico_especifico.py, en la raíz del repo)
con tres modos:
  - Solo genérico: extrae la tarifa del genérico elegido (TRFPO, GUIAPO,
    PEAPO...) para las locations marcadas y la guarda en el Google Sheet
    de salida (pestaña GENERICOS). Se hace una vez y queda archivada.
  - Solo específico: extrae la tarifa de uno o más proveedores
    específicos y la compara contra el genérico ya archivado que
    comparta location/código.
  - Completo: ambos en la misma corrida.
Además, "Actualizar variación" (para usar justo después de cambiar
valores en Tourplan) vuelve a leer sólo los proveedores que se indiquen
—o todos los específicos registrados— y recalcula la comparación.

El script corre como subproceso, parametrizado por variables de entorno
(mismo patrón que Drive-TP-NX-App). Usuario/password de Tourplan y la
URL del Sheet se guardan en ~/.tourplan-nx-app/config.json (fuera del
repo).
"""
import csv
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

import streamlit as st

from common import user_config
from common.abort import ABORT_EXIT_CODE

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
SCRIPT_PATH = REPO_ROOT / "variacion_generico_especifico.py"
CATALOGO_PATH = REPO_ROOT / "config" / "genericos.csv"

PRODUCCION_URL = "https://tourplannx.eurotur.com.ar/tourplannx"
SCRIPT_KEY = "variacion_gve"  # clave en config.json -> sheet_urls

MODOS = [
    ("Solo genérico (extrae y archiva el genérico)", "GENERICO"),
    ("Solo específico (compara contra el genérico archivado)", "ESPECIFICO"),
    ("Completo (genérico + específico en la misma corrida)", "COMPLETO"),
]


def cargar_catalogo():
    """{supplier: {"nombre": str, "locations": [str], "service_type": str}}
    a partir de config/genericos.csv (una fila por location+supplier)."""
    catalogo = {}
    with open(CATALOGO_PATH, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            sup = r["SUPPLIER"].strip()
            entrada = catalogo.setdefault(sup, {
                "nombre": r["SUPPLIER_NAME"].strip(),
                "locations": [],
                "service_type": r.get("SERVICE_TYPE", "").strip(),
            })
            loc = r["LOCATION"].strip()
            if loc not in entrada["locations"]:
                entrada["locations"].append(loc)
    return catalogo


def leer_registrados(sheet_url):
    """[(supplier, location, "genérico"/"específico")] de lo ya archivado
    en el Sheet de salida (pestañas GENERICOS y ESPECIFICOS)."""
    from common.sheets_client import conectar_sheets, cargar_sheet
    registrados = []
    for hoja, tipo in (("GENERICOS", "genérico"), ("ESPECIFICOS", "específico")):
        try:
            ws = conectar_sheets(sheet_url, hoja, user_config.CREDENTIALS_PATH, user_config.TOKEN_PATH)
        except ValueError:
            continue  # la pestaña todavía no existe (se crea en la primera corrida)
        filas, _ = cargar_sheet(ws)
        pares = {(f.get("SUPPLIER", "").strip(), f.get("LOCATION", "").strip()) for f in filas}
        registrados += [(s, l, tipo) for s, l in sorted(pares) if s and l]
    return registrados


def _leer_proceso(proc, state):
    """Hilo aparte: lee el stdout del subproceso sin bloquear Streamlit,
    para que el botón Abortar pueda reaccionar mientras corre."""
    for line in proc.stdout:
        state["log_lines"].append(line)
    proc.wait()
    state["returncode"] = proc.returncode
    state["finished"] = True


def render_configuracion():
    st.header("Configuración")
    st.caption("Se guarda en esta computadora (no se sube al repositorio).")
    cfg = user_config.cargar()
    with st.form("form_config"):
        tp_usuario = st.text_input("Usuario Tourplan", value=cfg.get("tp_usuario", ""))
        tp_password = st.text_input("Password Tourplan", value=cfg.get("tp_password", ""), type="password")
        tp_base_url = st.text_input(
            "URL de Tourplan", value=cfg.get("tp_base_url", PRODUCCION_URL),
            help="Producción por default. Para probar contra Test, cambiala acá "
                 "(ej. https://tourplannx.eurotur.com.ar/TourplanNX_Test).")
        sheet_url = st.text_input(
            "URL del Google Sheet de salida",
            value=cfg.get("sheet_urls", {}).get(SCRIPT_KEY, ""),
            help="Ahí se guardan las pestañas GENERICOS, ESPECIFICOS y COMPARACION.",
        )
        headless = st.checkbox("Correr Chrome sin ventana (headless)", value=bool(cfg.get("headless", False)))
        credenciales = st.file_uploader(
            "Credenciales de Google (credentials.json, OAuth de escritorio)", type="json",
            help="Hace falta una sola vez por PC para que la app pueda escribir en el Sheet. "
                 "Se guarda en tu carpeta de usuario, fuera del repo. La primera corrida abre "
                 "el navegador para dar permiso.")
        if os.path.exists(user_config.CREDENTIALS_PATH):
            st.caption("✅ Ya hay un credentials.json guardado en esta PC (subir otro lo reemplaza).")
        if st.form_submit_button("Guardar"):
            if credenciales is not None:
                os.makedirs(user_config.CONFIG_DIR, exist_ok=True)
                with open(user_config.CREDENTIALS_PATH, "wb") as f:
                    f.write(credenciales.getvalue())
            urls = dict(cfg.get("sheet_urls", {}))
            urls[SCRIPT_KEY] = sheet_url.strip()
            cfg.update({"tp_usuario": tp_usuario.strip(), "tp_password": tp_password,
                        "sheet_urls": urls, "headless": headless,
                        "tp_base_url": tp_base_url.strip() or PRODUCCION_URL})
            user_config.guardar(cfg)
            st.success("Configuración guardada.")


def _lanzar(state, env_extra):
    run_dir = Path(tempfile.mkdtemp(prefix="tourplan_gve_"))
    ss_dir = run_dir / "screenshots"
    ss_dir.mkdir(exist_ok=True)
    stop_file = run_dir / "ABORTAR.flag"
    usuario, password = user_config.tp_credenciales_default()

    env = os.environ.copy()
    env.update({
        "TOURPLAN_USERNAME": usuario,
        "TOURPLAN_PASSWORD": password,
        "TOURPLAN_CREDENTIALS_PATH": user_config.CREDENTIALS_PATH,
        "TOURPLAN_TOKEN_PATH": user_config.TOKEN_PATH,
        "TOURPLAN_HEADLESS": "1" if user_config.headless_default() else "0",
        "TOURPLAN_SS_DIR": str(ss_dir),
        "TOURPLAN_STOP_FILE": str(stop_file),
        "PYTHONPATH": str(APP_DIR) + os.pathsep + env.get("PYTHONPATH", ""),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        **env_extra,
    })
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT_PATH)], cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", bufsize=1,
    )
    state.update({"running": True, "finished": False, "log_lines": [], "returncode": None,
                  "proc": proc, "stop_file": stop_file, "abort_requested": False})
    threading.Thread(target=_leer_proceso, args=(proc, state), daemon=True).start()
    st.rerun()


def render_principal():
    catalogo = cargar_catalogo()
    todas_locations = sorted({l for e in catalogo.values() for l in e["locations"]})

    state = st.session_state.setdefault("_state_gve", {
        "running": False, "finished": False, "log_lines": [], "returncode": None,
        "abort_requested": False})

    usuario, password = user_config.tp_credenciales_default()
    sheet_url = user_config.sheet_url_default(SCRIPT_KEY)
    # La URL de Tourplan se carga una sola vez en ⚙️ Configuración.
    base_url = user_config.cargar().get("tp_base_url", PRODUCCION_URL)
    st.caption(f"Tourplan: {base_url} (se cambia en ⚙️ Configuración)")

    st.subheader("1. Modo")
    modo_label = st.radio("Modo", [m[0] for m in MODOS], label_visibility="collapsed",
                          disabled=state["running"])
    modo = dict(MODOS)[modo_label]

    st.subheader("2. Proveedor y locations")
    generico, locations_gen, especificos, locations_esp = "", [], "", []

    if modo in ("GENERICO", "COMPLETO"):
        generico = st.selectbox(
            "Supplier genérico", sorted(catalogo), index=None,
            placeholder="Elegí un supplier genérico",
            format_func=lambda s: f"{s} — {catalogo[s]['nombre']}",
            disabled=state["running"]) or ""
        if generico:
            disponibles = catalogo[generico]["locations"]
            # key por supplier: al cambiar de genérico se resetea la selección
            # (cada uno habilita solo las locations donde está cargado).
            locations_gen = st.multiselect(
                f"Locations de {generico} (buscá o elegí las que necesites)", disponibles, default=[],
                key=f"loc_gen_{generico}", disabled=state["running"])
            if not catalogo[generico]["service_type"]:
                st.caption(f"⚠️ {generico} no tiene Service Type definido en config/genericos.csv todavía.")
        else:
            st.caption("Elegí el supplier genérico para ver sus locations.")

    if modo in ("ESPECIFICO", "COMPLETO"):
        especificos = st.text_input(
            "Proveedor(es) específico(s) — código o nombre, separados por coma",
            placeholder="ej. 6HOUS1, 1TEP01", disabled=state["running"])
        if modo == "ESPECIFICO":
            locations_esp = st.multiselect(
                "Locations donde buscarlos", todas_locations, disabled=state["running"])
        else:
            locations_esp = locations_gen  # en Completo comparte las del genérico

    st.subheader("3. Rango de fechas a comparar")
    limitar = st.checkbox("Limitar a un rango (si no, solo el período vigente hoy)",
                          value=False, disabled=state["running"])
    desde = hasta = None
    if limitar:
        c1, c2 = st.columns(2)
        desde = c1.date_input("Desde", value=date.today(), format="DD/MM/YYYY", disabled=state["running"])
        hasta = c2.date_input("Hasta", value=date.today(), format="DD/MM/YYYY", disabled=state["running"])

    with st.expander("Opciones avanzadas"):
        price_code = st.text_input(
            "Price Code a leer en RATES", value="TR", disabled=state["running"],
            help="Confirmado sólo para TRFPO (TR). Para otros genéricos, si no lee bien "
                 "los valores, probá con el Price Code que corresponda (o ALL).")
        tipo_cambio = st.text_input(
            "Tipo de cambio ARS→USD (opcional)", value="", disabled=state["running"],
            help="Sin esto, las filas cargadas en ARS quedan sin TARIFA_USD y no se comparan.")

    # Validación mínima para habilitar "Ejecutar"
    if modo == "GENERICO":
        completo = bool(generico and locations_gen)
    elif modo == "ESPECIFICO":
        completo = bool(especificos.strip() and locations_esp)
    else:
        completo = bool(generico and locations_gen and especificos.strip())
    if limitar and desde and hasta and desde > hasta:
        completo = False
    base_ok = bool(usuario and password and sheet_url and base_url)

    if not SCRIPT_PATH.exists():
        st.info("variacion_generico_especifico.py todavía no está en el repo — la interfaz "
                "ya funciona pero no se puede ejecutar hasta que el script se sume.")
    if not base_ok:
        st.caption("Completá usuario/password y URL del Sheet en ⚙️ Configuración para poder ejecutar.")

    puede = completo and base_ok and SCRIPT_PATH.exists() and not state["running"]
    c_run, c_abort = st.columns(2)
    run_clicked = c_run.button("Ejecutar", type="primary", disabled=not puede, use_container_width=True)
    abort_clicked = c_abort.button(
        "⏹ Abortar", disabled=not state["running"] or state["abort_requested"], use_container_width=True)

    st.subheader("Actualizar variación")
    st.caption("Usalo justo después de modificar valores en Tourplan: indicá qué proveedor cambió y "
               "se vuelven a leer sólo esos (no todo lo anterior); la comparación se recalcula al terminar.")
    if st.button("↻ Leer proveedores registrados del Sheet",
                 disabled=not sheet_url or state["running"]):
        try:
            st.session_state["registrados"] = leer_registrados(sheet_url)
        except Exception as e:
            st.error(f"No pude leer el Sheet: {e}")
    registrados = st.session_state.get("registrados", [])
    if "registrados" in st.session_state and not registrados:
        st.info("Todavía no hay nada archivado en el Sheet — corré antes un modo genérico/específico/completo.")
    cambiados = st.multiselect(
        "Proveedores que cambiaron", registrados, disabled=state["running"] or not registrados,
        format_func=lambda r: f"{r[0]} — {r[1]} ({r[2]})")
    todos_esp = st.checkbox("Actualizar todos los específicos registrados (más lento)",
                            disabled=state["running"] or not registrados)
    upd_clicked = st.button(
        "🔄 Actualizar variación", disabled=not (base_ok and SCRIPT_PATH.exists())
        or state["running"] or not (cambiados or todos_esp))

    def _fmt(d):
        return d.strftime("%d/%m/%Y") if d else ""

    env_comun = {
        "TOURPLAN_BASE_URL": base_url,
        "TOURPLAN_SHEET_URL": sheet_url,
        "TOURPLAN_FECHA_DESDE": _fmt(desde),
        "TOURPLAN_FECHA_HASTA": _fmt(hasta),
        "TOURPLAN_PRICE_CODE": price_code.strip(),
        "TOURPLAN_TIPO_CAMBIO": tipo_cambio.strip(),
    }
    if run_clicked:
        _lanzar(state, {
            **env_comun,
            "TOURPLAN_MODO": modo,
            "TOURPLAN_GENERICO": generico,
            "TOURPLAN_GENERICO_SERVICE_TYPE": catalogo[generico]["service_type"] if generico else "",
            "TOURPLAN_LOCATIONS": ",".join(locations_gen if modo != "ESPECIFICO" else locations_esp),
            "TOURPLAN_ESPECIFICOS": especificos.strip(),
        })
    if upd_clicked:
        _lanzar(state, {
            **env_comun, "TOURPLAN_MODO": "ACTUALIZAR",
            "TOURPLAN_ACTUALIZAR": "" if todos_esp else ",".join(f"{r[0]}@{r[1]}" for r in cambiados),
        })

    if abort_clicked:
        state["abort_requested"] = True
        try:
            state["stop_file"].touch()
        except Exception:
            pass
    if state["abort_requested"] and state["running"]:
        st.warning("⏸️ Abortando: termina el código en curso, hace logout de Tourplan y corta.")

    if state["log_lines"]:
        st.code("".join(state["log_lines"][-500:]), language=None)

    if state["running"] and state["finished"]:
        state["running"] = False
        rc = state["returncode"]
        if rc == 0:
            st.success("Terminó OK. Revisá el resultado en el Sheet.")
        elif rc == ABORT_EXIT_CODE:
            st.info("⏸️ Abortado.")
        else:
            st.error(f"El proceso terminó con error (código {rc}). Revisá el log arriba.")
    if state["running"]:
        time.sleep(1)
        st.rerun()


def main():
    st.set_page_config(page_title="Genérico vs Específicos", layout="centered")
    st.title("Genérico vs Específicos")
    st.caption("Variación entre el costo genérico (TRFPO, GUIAPO, PEAPO...) y los tarifarios de cada proveedor específico.")
    if "vista" not in st.session_state:
        st.session_state["vista"] = "principal"
    if st.sidebar.button("⚙️ Configuración", use_container_width=True,
                         type="primary" if st.session_state["vista"] == "config" else "secondary"):
        st.session_state["vista"] = "config"
        st.rerun()
    if st.sidebar.button("▶ Comparación", use_container_width=True,
                         type="primary" if st.session_state["vista"] == "principal" else "secondary"):
        st.session_state["vista"] = "principal"
        st.rerun()
    if st.session_state["vista"] == "config":
        render_configuracion()
    else:
        render_principal()


if __name__ == "__main__":
    main()
