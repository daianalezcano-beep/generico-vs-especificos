# ============================================================
# GENÉRICO vs ESPECÍFICOS — script único de la app local (app/app.py)
# ------------------------------------------------------------
#   Extrae de Tourplan NX la tarifa vigente de un supplier genérico
#   (TRFPO, GUIAPO, PEAPO, ...) y/o de proveedores específicos, guarda
#   todo en un Google Sheet y calcula la variación genérico vs.
#   específico EN LA MISMA CORRIDA (la comparación se recalcula siempre
#   que se suma o cambia un dato, no hace falta un paso aparte).
#
#   Los helpers de Tourplan (login, búsqueda de productos, lectura de
#   RATES por scroll virtual, períodos, price code) y la comparación de
#   gap por vehículo (construir_comparacion_gap) son COPIA de
#   extraccion_tarifas_vigentes.py (ya validado contra Tourplan real
#   para TRFPO). Si se corrige un bug ahí, replicarlo acá y viceversa.
#   Lo propio de este script: configuración por variables de entorno
#   (la app las manda), varias locations por corrida, y el Google Sheet
#   como almacén en vez de Excel locales.
#
#   MODOS (TOURPLAN_MODO):
#     GENERICO   → extrae TOURPLAN_GENERICO en cada location de
#                  TOURPLAN_LOCATIONS y lo archiva en la pestaña
#                  GENERICOS. Después recalcula la comparación de esas
#                  locations con los específicos ya archivados.
#     ESPECIFICO → extrae los proveedores de TOURPLAN_ESPECIFICOS
#                  (código o nombre, separados por coma) en cada
#                  location y los compara contra el genérico elegido
#                  (TOURPLAN_GENERICO), ya archivado.
#     COMPLETO   → GENERICO + ESPECIFICO en la misma corrida.
#     ACTUALIZAR → re-extrae todos los específicos ya registrados en la
#                  pestaña ESPECIFICOS y recalcula la comparación.
#
#   PESTAÑAS del Sheet (TOURPLAN_SHEET_URL; se crean si no existen):
#     GENERICOS    tarifas archivadas de cada genérico (por supplier+location)
#     ESPECIFICOS  tarifas de cada específico — es también el registro de
#                  qué específicos actualizar con ACTUALIZAR
#     COMPARACION  gap genérico vs específico (una fila por código/pax/período)
#
#   GUARDADO CONSTANTE: las tarifas de cada código se escriben en el Sheet
#   apenas se leen (reemplazando sus filas archivadas). La COMPARACIÓN se
#   calcula al terminar TODOS los códigos de cada proveedor (mientras se
#   exporta el siguiente) y, al final, lo que haya quedado sin comparar
#   (también si se aborta o hay un error, con lo ya guardado); los códigos archivados que
#   ya no existen en Tourplan sólo se borran cuando el supplier se leyó
#   completo y sin fallas (no con TOURPLAN_LIMIT_PRUEBA ni si falló algún
#   código), para no degradar un archivo bueno con uno incompleto.
#
#   NAVEGACIÓN: con 2+ códigos del mismo supplier se usa el atajo de la
#   lupa de Product Search (portado de Drive-TP-NX-App), con caída a la
#   búsqueda completa ante cualquier duda.
#
#   SIN VALIDAR contra Tourplan real desde esta app: sólo TRFPO estaba
#   confirmado en el script original. Búsqueda de específico por NOMBRE
#   (no sólo código), Price Code distinto de "TR" para otros genéricos y
#   la comparación por vehículo fuera de transporte (GUIAPO, PEAPO,
#   BOXLPO...) pueden dar banderas en COMPARACION — ver DISENO.md.
# ============================================================

import os, sys, subprocess, importlib.util, shutil, time, re, traceback
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

import gspread
from common.chrome_bootstrap import find_or_prepare_chrome
from common.abort import chequear_abort, AbortadoPorUsuario, ABORT_EXIT_CODE
from common.sheets_client import _cargar_credenciales
from common.user_config import (
    CREDENTIALS_PATH as _CREDENTIALS_PATH_DEFAULT,
    TOKEN_PATH as _TOKEN_PATH_DEFAULT,
)

# Chrome se detecta recién al crear el driver (no al importar el módulo).
CHROMIUM_BIN, ver_chrome = None, ""


def _env_lista(nombre):
    return [x.strip() for x in os.environ.get(nombre, "").split(",") if x.strip()]


# ── Config — variables de entorno que manda app/app.py ───────────
USERNAME   = os.environ.get("TOURPLAN_USERNAME", "")
PASSWORD   = os.environ.get("TOURPLAN_PASSWORD", "")
BASE_URL   = os.environ.get("TOURPLAN_BASE_URL", "https://tourplannx.eurotur.com.ar/tourplannx")
SHEET_URL  = os.environ.get("TOURPLAN_SHEET_URL", "")
CREDENTIALS_PATH = os.environ.get("TOURPLAN_CREDENTIALS_PATH", _CREDENTIALS_PATH_DEFAULT)
TOKEN_PATH       = os.environ.get("TOURPLAN_TOKEN_PATH", _TOKEN_PATH_DEFAULT)
SS_DIR     = os.environ.get("TOURPLAN_SS_DIR", "screenshots")
HEADLESS   = os.environ.get("TOURPLAN_HEADLESS", "0").strip() in ("1", "true", "True")
os.makedirs(SS_DIR, exist_ok=True)

MODO             = os.environ.get("TOURPLAN_MODO", "").strip().upper()
GENERICO         = os.environ.get("TOURPLAN_GENERICO", "").strip()
SERVICE_TYPE_GENERICO = os.environ.get("TOURPLAN_GENERICO_SERVICE_TYPE", "").strip()
LOCATIONS        = _env_lista("TOURPLAN_LOCATIONS")
ESPECIFICOS      = _env_lista("TOURPLAN_ESPECIFICOS")
# Sólo para ACTUALIZAR: "SUPPLIER@LOCATION,..." — qué proveedores cambiaron
# en Tourplan (genéricos o específicos ya archivados). Vacío = todos los
# específicos registrados.
ACTUALIZAR_SELECCION = {tuple(x.rsplit("@", 1)) for x in _env_lista("TOURPLAN_ACTUALIZAR") if "@" in x}

# Rango de fechas a analizar ("dd/mm/yyyy"); vacío = sólo el período
# vigente hoy. Cada código puede tener varios períodos de RATES: se
# procesan TODOS los que se superponen con este rango.
PERIODO_ANALISIS_DESDE = os.environ.get("TOURPLAN_FECHA_DESDE", "").strip() or None
PERIODO_ANALISIS_HASTA = os.environ.get("TOURPLAN_FECHA_HASTA", "").strip() or None

# Price Code a filtrar en la lista de RATES antes de leer valores (en la
# vista "All Price Codes" Tourplan puede mostrar 0.0 silenciosamente).
# Confirmado sólo para TRFPO ("TR"). "ALL"/"" = no filtrar.
PRICE_CODE_DEFAULT = os.environ.get("TOURPLAN_PRICE_CODE", "TR").strip()

_tc = os.environ.get("TOURPLAN_TIPO_CAMBIO", "").strip().replace(",", ".")
TIPO_CAMBIO_ARS_USD = float(_tc) if _tc else None

LIMIT_PRUEBA = int(os.environ.get("TOURPLAN_LIMIT_PRUEBA", "0") or 0)
VELOCIDAD = float(os.environ.get("TOURPLAN_VELOCIDAD", "1.0") or 1.0)
MOSTRAR_CAPTURAS = False

HOJA_GENERICOS   = "GENERICOS"
HOJA_ESPECIFICOS = "ESPECIFICOS"
HOJA_COMPARACION = "COMPARACION"

_ss_n = [0]
_avisado_sin_ipython = [False]

# Códigos "genéricos" que no son tarifas reales de ningún servicio —
# siempre cargados en 0 a mano (confirmado para 600TRF/700TRF de
# TRFPO; MINWAT es el mismo caso). Se descartan ANTES de abrir el
# producto. Agregar acá los placeholders análogos de otros genéricos.
CODIGOS_EXCLUIR = {"600TRF", "700TRF", "MINWAT"}

# Además del código explícito, se descarta por texto de la descripción
# (cubre variantes que no estén en CODIGOS_EXCLUIR, ej. TRF19/24/42 —
# "TR Generico ...").
PATRONES_EXCLUIR_DESCRIPCION = [
    re.compile(r'MINERAL\s*WATER', re.I),
    re.compile(r'^\s*TRANSFER\s*$', re.I),
    re.compile(r'\bTR\s+GENERICO\b', re.I),
]


def _es_codigo_excluido(codigo, descripcion):
    if (codigo or "").strip().upper() in CODIGOS_EXCLUIR:
        return True
    return any(p.search(descripcion or "") for p in PATRONES_EXCLUIR_DESCRIPCION)


# ── Helpers base (REUTILIZADO tal cual de los scripts hermanos) ────

def ss(driver, nombre):
    _ss_n[0] += 1
    p = f"{SS_DIR}/{_ss_n[0]:03d}_{nombre[:40]}_{int(time.time())}.png"
    driver.save_screenshot(p)
    print(f"  📸 {os.path.basename(p)}")
    if MOSTRAR_CAPTURAS:
        try:
            from IPython.display import display, Image as IPyImage
            display(IPyImage(p, width=900))
        except ImportError:
            if not _avisado_sin_ipython[0]:
                print("    ⚠ MOSTRAR_CAPTURAS=True pero no hay IPython disponible "
                      "(sólo se ve inline en Colab/Jupyter) — se sigue guardando en disco.")
                _avisado_sin_ipython[0] = True


def dump(driver, nombre):
    p = f"{SS_DIR}/{nombre}_{int(time.time())}.html"
    with open(p, "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    print(f"  💾 HTML: {p}")


def jc(driver, el):
    """JavaScript click — único método confiable en Angular."""
    driver.execute_script("arguments[0].click();", el)


def set_val(driver, el, value):
    driver.execute_script("""
        var inp = arguments[0], val = arguments[1];
        var setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(inp, val);
        inp.dispatchEvent(new Event('input',  {bubbles:true}));
        inp.dispatchEvent(new Event('change', {bubbles:true}));
    """, el, value)


def wait(driver, css, t=12):
    return WebDriverWait(driver, t).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, css)))


def crear_driver():
    global CHROMIUM_BIN, ver_chrome
    import tempfile

    CHROMIUM_BIN, ver_chrome = find_or_prepare_chrome()

    opts = Options()
    # Sin ventana visible sólo si se pide (TOURPLAN_HEADLESS, checkbox en
    # Configuración) — por default corre con ventana real en la PC.
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,911")
    opts.add_argument("--disable-gpu")

    # Perfil temporal y aislado: evita que Chrome "rebote" hacia una
    # ventana ya abierta con los perfiles reales de la persona.
    _profile_dir = tempfile.mkdtemp(prefix="tourplan_chrome_profile_")
    opts.add_argument(f"--user-data-dir={_profile_dir}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")

    if CHROMIUM_BIN:
        opts.binary_location = CHROMIUM_BIN
    drv_path = ChromeDriverManager().install()
    svc = Service(executable_path=drv_path)
    d = webdriver.Chrome(service=svc, options=opts)
    print(f"✅ Driver iniciado (Chrome: {CHROMIUM_BIN or 'default del sistema'})")
    return d


def login(driver):
    print("🔐 Login...")
    driver.get(f"{BASE_URL}/#/login")
    time.sleep(6 * VELOCIDAD)
    ss(driver, "login_page")

    def _campos_visibles():
        return driver.execute_script("""
            function vis(e){return !!(e && (e.offsetWidth||e.offsetHeight
                                      ||e.getClientRects().length)
                                      && !e.disabled);}
            var txt = Array.from(document.querySelectorAll(
                "input[type='text'], input:not([type])")).filter(vis);
            var pwd = Array.from(document.querySelectorAll(
                "input[type='password']")).filter(vis);
            return [txt[0]||null, pwd[0]||null];
        """)

    u_el = p_el = None
    for _ in range(15):
        u_el, p_el = _campos_visibles()
        if u_el and p_el:
            break
        time.sleep(2)
    if not (u_el and p_el):
        ss(driver, "login_sin_campos")
        raise Exception("No aparecieron los campos de login (usuario/password)")

    def _set(el, valor):
        try: el.clear()
        except Exception: pass
        try: el.click()
        except Exception: pass
        try: el.send_keys(valor)
        except Exception:
            set_val(driver, el, valor)

    _set(u_el, USERNAME)
    _set(p_el, PASSWORD)
    time.sleep(0.5)

    clic = driver.execute_script("""
        function vis(e){return !!(e && (e.offsetWidth||e.offsetHeight
                                  ||e.getClientRects().length) && !e.disabled);}
        var b = Array.from(document.querySelectorAll(
            "button.login, button[type='submit'], button")).filter(vis)
            .find(function(x){return /log\\s*in|ingresar|entrar|sign\\s*in/i
                                     .test((x.innerText||'')) ||
                                     x.classList.contains('login');});
        if (b){ b.click(); return (b.innerText||'button.login').trim(); }
        return null;
    """)
    if not clic:
        try: p_el.send_keys(Keys.ENTER)
        except Exception: pass
    time.sleep(8 * VELOCIDAD)
    assert "login" not in driver.current_url.lower(), "Login falló"
    ss(driver, "post_login")
    print("✅ Login OK")


def hamburger(driver):
    img = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "nav img")))
    jc(driver, img)
    time.sleep(2.5 * VELOCIDAD)


def menu_item(driver, n_or_text):
    if isinstance(n_or_text, int):
        xpath = f"(//nav//ul/li)[{n_or_text}]/div/div"
    else:
        texto = n_or_text.upper()
        xpath = (f"//nav//ul/li[.//*[contains("
                 f"translate(normalize-space(.),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),"
                 f"'{texto}')]]/div/div")
    el = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.XPATH, xpath)))
    jc(driver, el)
    time.sleep(2 * VELOCIDAD)


# ── Búsqueda de producto (REUTILIZADO de cbd_impact_simulator_pkg.py —
# la versión que abre el resultado y confirma contexto) ────────────

STYPE_SIDEBAR = {
    "HT": "01", "HX": "02", "TF": "03", "EX": "04", "ML": "05",
    "RT": "06", "CR": "07", "FT": "08", "OC": "09", "LN": "10",
    "LP": "11", "MS": "12", "TA": "13", "PR": "14",
}


class ProductoNoEncontrado(Exception):
    pass


def _completar_filtros_busqueda(driver, location, supplier, codigo, service_type):
    """Navega a Product Search y completa Location/Supplier/Code(/Service
    Type) — todo lo que buscar_producto y listar_codigos_supplier
    necesitan en común, antes de divergir en qué hacer con el
    resultado (abrir un producto puntual vs. leer todos los códigos de
    la grilla). Adaptado tal cual de
    cbd_impact_simulator_pkg.py::buscar_producto — ver DISENO.md."""
    st_upper = (service_type or "").strip().upper()

    driver.get(f"{BASE_URL}/#/home")
    time.sleep(2 * VELOCIDAD)
    driver.get(f"{BASE_URL}/#/product")
    time.sleep(5 * VELOCIDAD)

    lupa = wait(driver, "#searchWrapper li:nth-of-type(2) button")
    jc(driver, lupa)
    time.sleep(3 * VELOCIDAD)

    try:
        WebDriverWait(driver, 4).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.parameters1 input")))
    except Exception:
        driver.execute_script("""
            var els = Array.from(document.querySelectorAll('li,button,a,div,span'));
            for (var el of els){
                if (!el.offsetParent) continue;
                var t = (el.innerText || '').trim().toUpperCase();
                if (t === 'SELECTION'){ el.click(); return true; }
            }
            return false;
        """)
        time.sleep(2 * VELOCIDAD)

    if st_upper:
        try:
            stype_num = STYPE_SIDEBAR.get(st_upper, "")
            _ok_st = False
            if stype_num:
                _ok_st = driver.execute_script("""
                    var num = arguments[0];
                    for (var li of document.querySelectorAll('li')){
                        if (!li.offsetParent) continue;
                        var t = (li.textContent || '').trim();
                        if (t.indexOf(num) >= 0){ li.click(); return t.slice(0,40); }
                    }
                    return null;
                """, stype_num)
                if _ok_st:
                    time.sleep(1.5 * VELOCIDAD)
            if not _ok_st:
                patron_js = r"(^|[\s\-–—/(])" + re.escape(st_upper) + r"([\s\-–—/)]|$)"
                resultado = None
                for _ in range(3):
                    resultado = driver.execute_script("""
                        var rx = new RegExp(arguments[0]);
                        var elegido = null;
                        for (var li of document.querySelectorAll('li')){
                            if (!li.offsetParent) continue;
                            var t = (li.textContent || '').trim();
                            if (!t || t.length > 80) continue;
                            if (!elegido && rx.test(t.toUpperCase())) elegido = li;
                        }
                        if (elegido){ elegido.click(); return true; }
                        return false;
                    """, patron_js)
                    if resultado:
                        break
                    time.sleep(1.5 * VELOCIDAD)
        except Exception as e:
            print(f"    ⚠ Error seleccionando service type: {e}")

    if location:
        try:
            inp_loc = wait(driver, "div.parameters1 li:nth-of-type(1) input")
            inp_loc.click(); time.sleep(0.3)
            set_val(driver, inp_loc, location)
            time.sleep(1.5 * VELOCIDAD)
            row = WebDriverWait(driver, 5).until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "tr.selectedRow > td.description")))
            jc(driver, row); time.sleep(1)
        except Exception:
            pass

    if supplier:
        try:
            inp_sup = wait(driver, "div.parameters1 li:nth-of-type(2) input")
            inp_sup.click(); time.sleep(0.3)
            set_val(driver, inp_sup, supplier)
            time.sleep(1.5 * VELOCIDAD)
            row_sup = WebDriverWait(driver, 5).until(EC.presence_of_element_located(
                (By.XPATH, f"//div[contains(@class,'parameters1')]//td[normalize-space(text())='{supplier.upper()}']")))
            jc(driver, row_sup); time.sleep(1)
        except Exception:
            try:
                inp_sup2 = driver.find_element(By.CSS_SELECTOR, "div.parameters1 li:nth-of-type(2) input")
                inp_sup2.send_keys(Keys.TAB); time.sleep(1)
            except Exception:
                pass

    if codigo:
        inp_cod = wait(driver, "div.parameters1 li:nth-of-type(3) input")
        inp_cod.click(); time.sleep(0.3)
        set_val(driver, inp_cod, codigo)
        time.sleep(0.5)

    btn_search = wait(driver, "#productSearchFilter li:nth-of-type(4) button")
    jc(driver, btn_search)
    time.sleep(6 * VELOCIDAD)


def buscar_producto(driver, location, supplier, codigo, service_type=None):
    """Busca Location/Supplier/Code(/ServiceType) en Product Search,
    abre el resultado y confirma que quedó en contexto de producto
    (menú con RATES/UTILITIES/etc.)."""
    codigo = str(codigo).strip() if codigo not in (None, "") else ""
    location = str(location).strip() if location not in (None, "") else ""
    supplier = str(supplier).strip() if supplier not in (None, "") else ""
    st_upper = (service_type or "").strip().upper()
    print(f"\n  📦 Buscando: {location}/{supplier}/{codigo}"
          f"{('/' + st_upper) if st_upper else ''}")

    _completar_filtros_busqueda(driver, location, supplier, codigo, service_type)

    def _en_contexto_producto():
        try:
            img = WebDriverWait(driver, 4).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "nav img")))
            jc(driver, img)
            time.sleep(2 * VELOCIDAD)
            items = driver.execute_script("""
                return Array.from(document.querySelectorAll('nav ul > li')).map(function(li){
                    return (li.querySelector('div div')||li).innerText.trim().split('\\n')[0].toUpperCase();
                });
            """) or []
            ok = "RATES" in items
            jc(driver, img)
            time.sleep(1)
            return ok, items
        except Exception:
            return False, []

    clicked = driver.execute_script("""
        var cod = (arguments[0] || '').toUpperCase();
        var st  = (arguments[1] || '').toUpperCase();
        var rows = Array.from(document.querySelectorAll('table tbody tr'));
        function matchRow(tr){
            var tds = Array.from(tr.querySelectorAll('td'));
            if (cod){
                var hasCod = tds.some(function(td){
                    return td.children.length===0 && td.innerText.trim().toUpperCase()===cod;
                });
                if(!hasCod) return false;
            }
            if(!st) return true;
            return tds.some(function(td){
                return td.children.length===0 && td.innerText.trim().toUpperCase()===st;
            });
        }
        var match = null;
        for (var tr of rows){ if (matchRow(tr)){ match = tr; break; } }
        if (!match && cod){
            for (var tr of rows){
                if ((tr.innerText||'').toUpperCase().includes(cod)){ match = tr; break; }
            }
        }
        if (!match && !cod && rows.length){ match = rows[0]; }
        if (!match) return null;
        var tds = match.querySelectorAll('td');
        var target = tds.length > 4 ? tds[4] : (tds.length > 0 ? tds[tds.length-1] : match);
        target.click();
        return target.innerText.trim().slice(0,50) || 'clicked';
    """, codigo or "", st_upper)

    if not clicked:
        ss(driver, f"ps_sin_resultado_{(codigo or 'sin_codigo')[:10]}")
        raise ProductoNoEncontrado(
            f"Producto no encontrado (location={location!r} supplier={supplier!r} "
            f"codigo={codigo!r} service_type={service_type!r})")

    time.sleep(6 * VELOCIDAD)
    ok_ctx, menu_items = _en_contexto_producto()
    if not ok_ctx:
        ss(driver, f"ps_contexto_incorrecto_{(codigo or 'sin_codigo')[:10]}")
        raise ProductoNoEncontrado(
            f"Click en resultado OK pero no quedó en contexto de producto "
            f"(menú visto: {menu_items}) — codigo={codigo!r}")


def _scrapear_pagina_resultados(driver):
    """Lee la página de resultados de Product Search actualmente
    visible: {items: [{codigo, descripcion}], paginado, total_filas,
    headers}. Columna de código identificada por HEADER de la tabla
    (el `th` cuyo texto es "Code", no cualquier celda corta que
    "parezca" un código — eso confundía la columna Location, ej.
    "BUE", con el código real)."""
    return driver.execute_script(r"""
        var tables = Array.from(document.querySelectorAll('table'));
        var target = null, headers = [];
        for (var t of tables){
            var ths = Array.from(t.querySelectorAll('th')).map(h => h.innerText.trim());
            if (ths.some(h => /CODE/i.test(h))){ target = t; headers = ths; break; }
        }
        if (!target){
            for (var t of tables){
                if (t.querySelectorAll('tbody tr').length){ target = t; break; }
            }
        }
        if (!target) return {items: [], paginado: false, total_filas: 0, headers: []};

        var idxCode = headers.findIndex(h => /^CODE$/i.test(h));
        if (idxCode < 0) idxCode = headers.findIndex(h => /CODE/i.test(h) && !/SERVICE/i.test(h));
        var idxLoc  = headers.findIndex(h => /LOCATION/i.test(h));
        var idxDesc = headers.findIndex(h => /DESCRIPTION/i.test(h));

        var rows = Array.from(target.querySelectorAll('tbody tr'));
        var out = rows.map(function(tr){
            var tds = Array.from(tr.querySelectorAll('td'));
            var codigo, descripcion;
            if (idxCode >= 0 && tds[idxCode]){
                codigo = tds[idxCode].innerText.trim();
            } else {
                var textos = tds.map(function(td){ return td.innerText.trim(); }).filter(Boolean);
                codigo = textos.find(function(t, i){
                    return /^[A-Z0-9]{2,10}$/.test(t) && i !== idxLoc;
                }) || '';
            }
            if (idxDesc >= 0 && tds[idxDesc]){
                descripcion = tds[idxDesc].innerText.trim();
            } else {
                var textos2 = tds.map(function(td){ return td.innerText.trim(); }).filter(Boolean);
                descripcion = textos2.reduce(function(a, b){ return b.length > a.length ? b : a; }, '');
            }
            return {codigo: codigo, descripcion: descripcion};
        }).filter(function(r){ return r.codigo; });

        var paginado = !!document.querySelector("[class*='pagin' i], [class*='pager' i]");
        return {items: out, paginado: paginado, total_filas: rows.length, headers: headers};
    """)


def _hacer_scroll_resultados(driver):
    """Avanza un paso de scroll en el contenedor de resultados —
    confirmado por captura real que la grilla de Product Search NO
    pagina con botón "siguiente": es una lista larga con scroll (virtual
    — sólo las filas visibles están en el DOM, por eso antes se leían
    siempre ~20-22 códigos, el tamaño del viewport, no el catálogo
    completo). Busca el ancestro con scroll real más cercano a la
    tabla y le corre el scrollTop un clientHeight. Devuelve True si
    pudo avanzar, False si ya está en el fondo."""
    return bool(driver.execute_script("""
        function contenedorScroll(){
            var fila = document.querySelector('table tbody tr');
            if (!fila) return null;
            var cur = fila.closest('table');
            while (cur && cur !== document.body){
                if (cur.scrollHeight > cur.clientHeight + 5) return cur;
                cur = cur.parentElement;
            }
            return document.scrollingElement || document.body;
        }
        var c = contenedorScroll();
        if (!c) return false;
        var antes = c.scrollTop;
        c.scrollTop = c.scrollTop + c.clientHeight;
        return c.scrollTop > antes;
    """))


def listar_codigos_supplier(driver, location, supplier, service_type=None):
    """Busca Location/Supplier(/ServiceType) en Product Search SIN
    código (deja ese campo vacío) y devuelve TODOS los resultados —
    scrolleando la grilla (virtual scroll, sin botón de paginación,
    ver _hacer_scroll_resultados) hasta que dejan de aparecer códigos
    nuevos — como lista de {codigo, descripcion}. Es lo que le permite
    a este script no necesitar ningún CSV/Excel de entrada: le alcanza
    con (location, supplier) — ver COMPARACIONES."""
    location = str(location).strip() if location not in (None, "") else ""
    supplier = str(supplier).strip() if supplier not in (None, "") else ""
    print(f"\n  📋 Listando códigos: {location}/{supplier}"
          f"{('/' + service_type.upper()) if service_type else ''}")

    _completar_filtros_busqueda(driver, location, supplier, "", service_type)
    ss(driver, f"listado_{supplier[:15]}")

    items_por_codigo = {}
    headers_vistos = []
    MAX_INTENTOS = 300
    UMBRAL_SIN_NUEVOS = 5
    intentos_sin_nuevos = 0
    for intento in range(1, MAX_INTENTOS + 1):
        resultado = _scrapear_pagina_resultados(driver)
        if not resultado:
            break
        headers_vistos = resultado.get("headers") or headers_vistos
        nuevos = 0
        for item in resultado.get("items", []):
            if item["codigo"] not in items_por_codigo:
                items_por_codigo[item["codigo"]] = item
                nuevos += 1
        if nuevos:
            print(f"    scroll {intento}: {nuevos} códigos nuevos "
                  f"({len(items_por_codigo)} acumulados)")
            intentos_sin_nuevos = 0
        else:
            intentos_sin_nuevos += 1

        # UMBRAL_SIN_NUEVOS scrolls seguidos sin nada nuevo = asumimos
        # que llegamos al final. Subido de 3 a 5 tras un caso real
        # donde la lista quedó corta (HTHT/HTMP/NEZH de TRFPO no se
        # descubrieron en una corrida, sí en otra) — el scroll virtual
        # a veces tarda más en renderizar de lo esperado, sin garantía
        # firme; más margen reduce el riesgo de cortar temprano.
        if intentos_sin_nuevos >= UMBRAL_SIN_NUEVOS:
            break
        if not _hacer_scroll_resultados(driver):
            if intentos_sin_nuevos >= 2:
                break
        time.sleep(1.5 * VELOCIDAD)
    else:
        print(f"    ⚠ Llegué al tope de {MAX_INTENTOS} scrolls para "
              f"{location}/{supplier} — puede haber más códigos sin leer.")

    # Pasada final defensiva: saltar directo al fondo del contenedor
    # (scrollHeight, no +clientHeight) y releer una vez más, por si el
    # loop incremental de arriba cortó antes de llegar al final real.
    driver.execute_script("""
        var fila = document.querySelector('table tbody tr');
        if (!fila) return;
        var cur = fila.closest('table');
        while (cur && cur !== document.body){
            if (cur.scrollHeight > cur.clientHeight + 5){ cur.scrollTop = cur.scrollHeight; return; }
            cur = cur.parentElement;
        }
    """)
    time.sleep(2 * VELOCIDAD)
    resultado_final = _scrapear_pagina_resultados(driver)
    if resultado_final:
        headers_vistos = resultado_final.get("headers") or headers_vistos
        nuevos_final = 0
        for item in resultado_final.get("items", []):
            if item["codigo"] not in items_por_codigo:
                items_por_codigo[item["codigo"]] = item
                nuevos_final += 1
        if nuevos_final:
            print(f"    ⚠ Pasada final (scroll directo al fondo) encontró "
                  f"{nuevos_final} códigos que el loop incremental se había "
                  f"salteado — revisar VELOCIDAD/UMBRAL_SIN_NUEVOS si esto "
                  f"se repite seguido.")

    if not items_por_codigo:
        dump(driver, f"listado_vacio_{supplier[:15]}")
        print(f"    ⚠ No encontré resultados para {location}/{supplier} "
              f"(headers vistos: {headers_vistos})")
        return []
    print(f"    → {len(items_por_codigo)} códigos encontrados en total "
          f"(headers: {headers_vistos})")
    return list(items_por_codigo.values())


# ── Períodos de RATES (REUTILIZADO tal cual de
# tourplan_valorizacion_pkg_v3.py::actualizar_rates_servicio_madre —
# _leer_periodos/_parse_rate_period/_seleccionar_price_code, selectores
# confirmados contra Tourplan real: td.tpcol-rateperiod,
# td.tpcol-pricecodecode, #priceCodeModeSelected, #priceCode) ────────

MESES_ES = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
            "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


def parsear_fecha(txt):
    """'01/Apr/2026', '01/04/2026' o '2026-04-01' → datetime. None si
    no matchea ningún formato conocido."""
    if not txt:
        return None
    if isinstance(txt, datetime):
        return txt
    txt = str(txt).strip()
    if len(txt) >= 10 and txt[4] == "-" and txt[7] == "-":
        try:
            return datetime.fromisoformat(txt[:10])
        except Exception:
            pass
    p = txt.split("/")
    if len(p) == 3:
        try:
            dia = int(p[0])
            mes_raw = p[1]
            mes = MESES_ES.get(mes_raw[:3].capitalize()) or int(mes_raw)
            anio_raw = int(p[2])
            anio = anio_raw + 2000 if anio_raw < 100 else anio_raw
            return datetime(anio, mes, dia)
        except Exception:
            pass
    return None


def _parse_rate_period(texto):
    """Parsea '01/Apr/2026 - 31/Aug/2026' → (datetime, datetime)."""
    m = re.search(r'(\d+/\w+/\d+)\s*[-–]\s*(\d+/\w+/\d+)', texto or "")
    if m:
        return parsear_fecha(m.group(1)), parsear_fecha(m.group(2))
    return None, None


def _seleccionar_price_code(driver, code, etiqueta=""):
    """En la lista de RATES (o dentro de un período ya abierto), pasa
    el modo a 'Selected Price Code' y elige el price code indicado.
    En modo 'All Price Codes' (default) la grilla es la vista agregada
    y lo que se lee ahí NO es lo persistido (puede leerse 0.0 sin
    ningún error — hallazgo real del script hermano). code vacío o
    "ALL"/"UNASSIGNED" no hace nada (se deja la vista default)."""
    code = (code or "").strip().upper()
    if not code or code in ("ALL", "TODOS", "*", "UNASSIGNED"):
        return False
    try:
        driver.execute_script("""
            var lbl = document.querySelector('label[for="priceCodeModeSelected"]');
            var inp = document.getElementById('priceCodeModeSelected');
            if (lbl) lbl.click();
            if (inp) inp.click();
        """)
        time.sleep(1.5 * VELOCIDAD)
        driver.execute_script("""
            var dd = document.getElementById('priceCode');
            if (!dd) return;
            var inp = dd.querySelector('input[type="text"], input');
            if (inp){ inp.focus(); inp.click(); } else { dd.click(); }
        """)
        time.sleep(1.0 * VELOCIDAD)
        driver.execute_script("""
            var dd = document.getElementById('priceCode');
            if (!dd) return;
            var inp = dd.querySelector('input[type="text"], input');
            if (!inp) return;
            var setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(inp, arguments[0]);
            inp.dispatchEvent(new Event('input', {bubbles:true}));
            inp.dispatchEvent(new Event('keyup', {bubbles:true}));
        """, code)
        time.sleep(1.2 * VELOCIDAD)
        elegido = driver.execute_script("""
            var pc = arguments[0].toUpperCase();
            var dd = document.getElementById('priceCode');
            if (!dd) return null;
            function vis(e){return !!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);}
            function match(tr){
                var cod = tr.querySelector('td.code');
                var des = tr.querySelector('td.description');
                var ct = cod ? (cod.innerText||cod.textContent||'').trim().toUpperCase() : '';
                var dt = des ? (des.innerText||des.textContent||'').trim().toUpperCase() : '';
                if (ct===pc || dt===pc || dt.indexOf(pc+' ')===0 ||
                    dt.indexOf(pc+'-')===0 || dt.indexOf(pc+' -')===0)
                    return {tr:tr, txt:ct + ' | ' + dt};
                return null;
            }
            var rows = Array.from(dd.querySelectorAll('table tr'));
            var cand = null;
            for (var tr of rows){ if (vis(tr)){ var m=match(tr); if(m){cand=m;break;} } }
            if (!cand){ for (var tr2 of rows){ var m2=match(tr2); if(m2){cand=m2;break;} } }
            if (!cand) return null;
            try{ cand.tr.scrollIntoView({block:'center'}); }catch(e){}
            var des = cand.tr.querySelector('td.description');
            var cod = cand.tr.querySelector('td.code');
            (des||cod||cand.tr).click();
            cand.tr.click();
            return cand.txt;
        """, code)
        time.sleep(2.5 * VELOCIDAD)
        print(f"    Price Code '{code}' seleccionado ({etiqueta}): {elegido}")
        if not elegido:
            ss(driver, f"pricecode_fail_{(etiqueta or code)[:12]}")
        return bool(elegido)
    except Exception as e:
        print(f"    ⚠ No pude seleccionar Price Code '{code}' ({etiqueta}): {e}")
        return False


def _leer_periodos_rates(driver):
    """Lista de {texto, desde, hasta, price_code, moneda} de la grilla
    de RATES actualmente visible (lista de períodos, no el detalle de
    uno). Cada fila trae la fecha en td.tpcol-rateperiod y su price
    code en td.tpcol-pricecodecode (selectores reales confirmados). La
    moneda (Buy/Sell Currency, iguales por default — confirmado por la
    usuaria) se lee ACÁ, en esta lista, y no de la grilla de un período
    ya abierto: ahí la moneda viene embebida en el texto del header
    (ej. "USD\\nGROUP COST"), que no es una fuente confiable en
    general. Se busca la columna por header ("CURRENCY", prioridad a
    "BUY") en la misma tabla que contiene los td.tpcol-rateperiod."""
    filas = driver.execute_script("""
        var celda0 = document.querySelector('td.tpcol-rateperiod');
        var tabla = celda0 ? celda0.closest('table') : null;
        var headers = tabla ? Array.from(tabla.querySelectorAll('th')).map(function(h){
            return h.innerText.trim();
        }) : [];
        var idxCcy = headers.findIndex(function(h){ return /BUY/i.test(h) && /CURR/i.test(h); });
        if (idxCcy < 0) idxCcy = headers.findIndex(function(h){ return /CURR/i.test(h); });

        var out = [];
        document.querySelectorAll('td.tpcol-rateperiod').forEach(function(d){
            var tr = d.closest('tr');
            var p = tr ? tr.querySelector('td.tpcol-pricecodecode') : null;
            var tds = tr ? Array.from(tr.querySelectorAll('td')) : [];
            var moneda = (idxCcy >= 0 && tds[idxCcy]) ? tds[idxCcy].innerText.trim() : '';
            out.push({texto: (d.innerText||'').trim(),
                      price_code: p ? (p.innerText||'').trim() : '',
                      moneda: moneda, headers: headers});
        });
        return out;
    """) or []
    out = []
    for f in filas:
        desde, hasta = _parse_rate_period(f["texto"])
        out.append({"texto": f["texto"], "price_code": f["price_code"],
                    "moneda": f["moneda"].strip().upper(), "desde": desde, "hasta": hasta})
    if filas and not any(f["moneda"] for f in filas):
        print(f"    ⚠ No encontré columna de moneda (BUY/SELL CURRENCY) en la lista "
              f"de períodos — headers vistos: {filas[0].get('headers')}")
    return out


def _periodos_en_rango(periodos, desde_str, hasta_str):
    """Índices de `periodos` cuyo rango de fechas se superpone con
    [desde_str, hasta_str]. Si ambos son None, usa HOY como rango de
    un solo día (o sea, el período vigente ahora mismo). Períodos sin
    fecha parseable (texto inesperado) se descartan con aviso, no se
    incluyen "por si acaso"."""
    objetivo_desde = parsear_fecha(desde_str) if desde_str else datetime.now()
    objetivo_hasta = parsear_fecha(hasta_str) if hasta_str else datetime.now()
    idxs = []
    for i, p in enumerate(periodos):
        if not (p["desde"] and p["hasta"]):
            print(f"    ⚠ Período con fecha no parseable, se descarta: {p['texto']!r}")
            continue
        if p["desde"] <= objetivo_hasta and p["hasta"] >= objetivo_desde:
            idxs.append(i)
    return idxs


# ── Lectura de la grilla RATES del componente (REUTILIZADO de
# tourplan_valorizacion_pkg_v3.py — _leer_tabla_rates/_extraer_valores_ad,
# generalizado para devolver el rango de pax desde/hasta en vez de sólo
# la etiqueta de fila) ──────────────────────────────────────────────

def _leer_tabla_rates(driver):
    """Lee la grilla de rangos de pax de RATES (hasta 24 filas) con
    manejo de scroll virtual (Angular CDK) — ver skill
    recorriendo-grillas-virtuales-de-tourplan. Bug real encontrado por
    la usuaria: sin este manejo, para un genérico con varios pax breaks
    (ej. TRFPO con "1-10 AD", "11-20 AD", etc.) sólo se capturaba la
    fila visible en el viewport en ese momento — normalmente la
    primera — perdiendo el resto de los rangos de pax silenciosamente
    (sin error, sólo una tarifa incorrecta al comparar contra un
    específico de otro rango). Se deduplica por el contenido de las
    celdas (la etiqueta de rango de pax, ej. "1-10 AD", es única por
    fila) en vez de por índice/posición, igual que el resto de las
    grillas virtuales de Tourplan."""

    def _leer_headers():
        return driver.execute_script("""
            for(var t of document.querySelectorAll('table')){
                var ths = Array.from(t.querySelectorAll('th'));
                if(ths.some(h => h.innerText.includes('GROUP COST') ||
                                  h.innerText.includes('COST'))){
                    return ths.map(h => h.innerText.trim());
                }
            }
            return null;
        """)

    def _leer_filas_visibles():
        return driver.execute_script("""
            for(var t of document.querySelectorAll('table')){
                var ths = Array.from(t.querySelectorAll('th'));
                if(ths.some(h => h.innerText.includes('GROUP COST') ||
                                  h.innerText.includes('COST'))){
                    return Array.from(t.querySelectorAll('tbody tr')).map(tr => ({
                        celdas: Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim()),
                        inputs: Array.from(tr.querySelectorAll('input')).map(i => i.value.trim())
                    }));
                }
            }
            return [];
        """)

    def _contenedor_scroll():
        return driver.execute_script("""
            var cdk = document.querySelector('cdk-virtual-scroll-viewport');
            if(cdk) return cdk;
            var candidates = ['[class*="rates"]', 'tp-rates', '[class*="rate"]'];
            for(var s of candidates){
                var el = document.querySelector(s);
                if(el && el.scrollHeight > el.clientHeight) return el;
            }
            var tbl = document.querySelector('table');
            if(tbl){
                var el = tbl.parentElement;
                while(el && el !== document.body){
                    var st = getComputedStyle(el);
                    var canScroll = (st.overflow === 'auto' || st.overflow === 'scroll' ||
                                     st.overflowY === 'auto' || st.overflowY === 'scroll');
                    if(canScroll && el.scrollHeight > el.clientHeight + 10) return el;
                    el = el.parentElement;
                }
            }
            return document.scrollingElement || document.body;
        """)

    def _scroll_a(pos, container):
        if container:
            driver.execute_script("arguments[0].scrollTop = arguments[1];", container, pos)
        driver.execute_script("window.scrollTo(0, arguments[0]);", pos)

    headers = _leer_headers()
    if not headers:
        return None

    scroll_container = _contenedor_scroll()
    filas_vistas = {}
    scroll_pos = 0
    scroll_step = 60
    sin_cambio = 0
    # Como mucho 24 pax breaks conocidos en Rates — 60 pasos de 60px
    # (3.600px) sobra de margen, con corte a las 4 iteraciones
    # consecutivas sin filas nuevas (mismo criterio que
    # leer_pcm_list_package_header en Used In).
    for _ in range(60):
        nuevas = 0
        for row in _leer_filas_visibles():
            celdas = row.get("celdas") or []
            clave = " | ".join(celdas)
            if clave and clave not in filas_vistas:
                filas_vistas[clave] = row
                nuevas += 1
        if nuevas == 0:
            sin_cambio += 1
            if sin_cambio >= 4:
                break
        else:
            sin_cambio = 0
        scroll_pos += scroll_step
        _scroll_a(scroll_pos, scroll_container)
        time.sleep(0.2 * VELOCIDAD)

    _scroll_a(0, scroll_container)
    return {"headers": headers, "rows": list(filas_vistas.values())}


def _listar_tablas_pagina(driver):
    """Diagnóstico: headers de TODAS las tablas visibles en la página
    actual — para cuando _leer_tabla_rates no encuentra ninguna con
    columna COST, y hay que ver a ciegas qué hay en pantalla (ej. una
    lista de períodos en vez de la grilla de tarifas) sin necesitar el
    HTML dump completo."""
    return driver.execute_script("""
        return Array.from(document.querySelectorAll('table')).map(function(t){
            return Array.from(t.querySelectorAll('th')).map(function(h){ return h.innerText.trim(); });
        });
    """)


def _detectar_moneda_headers(headers):
    """Fallback si la lista de períodos no trajo moneda: la grilla de
    un período abierto la muestra embebida en el texto del header (ej.
    "USD\\nGROUP COST") — no es la fuente principal (ver
    _leer_periodos_rates) porque no se confirmó que sea confiable en
    todos los casos, pero sirve de respaldo."""
    for h in headers:
        m = re.match(r'^([A-Z]{3})\b', h.strip())
        if m:
            return m.group(1)
    return ""


def _parsear_numero(raw):
    """Convierte el texto de una celda de tarifa a float. Tourplan usa
    formato US/UK para TODAS las monedas que se vieron hasta ahora —
    coma = separador de miles, punto = decimal — confirmado con un
    valor real de 6HOUS1 en ARS: "85,000" (ochenta y cinco mil pesos;
    si la coma fuera decimal sería "85 pesos", sin sentido para un
    transfer). Antes se intentaba adivinar cuál separador era el
    decimal por cuál aparecía más a la derecha (asumiendo que ARS podía
    venir en formato argentino, punto de miles + coma decimal) — con
    "85,000" (sin punto) ese heurístico lo interpretaba mal como 85.0.
    Ahora se asume siempre esta única convención, para USD y para ARS."""
    s = str(raw).replace("$", "").replace(" ", "").strip()
    if not s:
        return None
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _extraer_filas_ad(tabla, codigo, moneda_periodo=""):
    """De una tabla ya leída con _leer_tabla_rates, devuelve las filas
    de rango de pax adulto (AD) como {pax_desde, pax_hasta, tarifa,
    moneda} — igual criterio que _extraer_valores_ad del script
    hermano. La moneda se recibe del período (ver
    _leer_periodos_rates) en vez de buscarse en esta grilla; sólo si
    viene vacía se intenta el fallback de header. No toca
    período/price code, eso lo maneja el llamador."""
    headers = tabla["headers"]
    idx_svc = next((i for i, h in enumerate(headers) if "SERVICE" in h.upper()), 0)
    idx_cost = next((i for i, h in enumerate(headers)
                      if "GROUP COST" in h.upper() and "FIT" not in h.upper()), 1)
    pat_rango = re.compile(r'(\d+)\s*[-–]\s*(\d+)')

    moneda = moneda_periodo or _detectar_moneda_headers(headers)
    if not moneda:
        print(f"    ⚠ No pude determinar la moneda de {codigo} (ni en la lista de "
              f"períodos ni en los headers de la grilla) — headers: {headers}")

    out = []
    for row in tabla["rows"]:
        celdas = row["celdas"]
        if not celdas:
            continue
        svc = celdas[idx_svc] if idx_svc < len(celdas) else ""
        if not svc.strip():
            continue
        m = pat_rango.search(svc)
        if not (m and re.search(r'\bAD\b', svc.upper())):
            continue
        pax_hasta = int(m.group(2))
        if pax_hasta == 9999:
            # Catch-all de pax abierto hacia arriba (ej. "42-9999 AD")
            # — confirmado por la usuaria que siempre da 0, no aporta
            # nada a la comparación y sólo infla la cantidad de filas.
            continue
        raw = row["inputs"][0] if (row["inputs"] and row["inputs"][0]) else (
            celdas[idx_cost] if idx_cost < len(celdas) else "")
        valor = _parsear_numero(raw)
        out.append({
            "pax_desde": int(m.group(1)),
            "pax_hasta": pax_hasta,
            "tarifa": round(valor, 2) if valor is not None else None,
            "moneda": moneda,
        })
    return out


def _abrir_lista_rates(driver, codigo, etiqueta, price_code):
    """Navega (o vuelve a navegar) a RATES desde el producto en
    contexto, y aplica el filtro de price_code si corresponde. Se llama
    de nuevo por cada período a abrir porque, al entrar al detalle de
    un período, no queda forma confirmada de "volver" a la lista salvo
    renavegar (mismo patrón de re-navegación que usa
    tourplan_valorizacion_pkg_v3.py en su verificación post-SAVE)."""
    hamburger(driver)
    menu_item(driver, "RATES")
    time.sleep(3 * VELOCIDAD)
    if price_code:
        _seleccionar_price_code(driver, price_code, etiqueta=etiqueta)
        time.sleep(1.5 * VELOCIDAD)
    ss(driver, f"rates_lista_{codigo[:10]}")


def leer_tarifa_vigente_componente(driver, codigo, price_code=None):
    """Abre el tab RATES del producto ya en contexto (ver
    buscar_producto), identifica qué período(s) caen dentro de
    [PERIODO_ANALISIS_DESDE, PERIODO_ANALISIS_HASTA] (por defecto, sólo
    el período vigente hoy — ver esa constante), abre cada uno y lee su
    grilla de tarifas. Devuelve una lista de {pax_desde, pax_hasta,
    tarifa, moneda, periodo_desde, periodo_hasta, price_code} — un
    conjunto de filas AD por cada período que matcheó.

    price_code: filtro de Price Code a aplicar en RATES — viene de la
    relación genérico/específico de COMPARACIONES (cada una puede tener
    el suyo, ej. "TR" para TRFPO; otra relación como PEAPO/GUIAPO puede
    necesitar uno distinto o "ALL"). None/"" = usa PRICE_CODE_DEFAULT.

    Moneda: cada período de Rates tiene columnas BUY CURRENCY y SELL
    CURRENCY, por defecto cargadas iguales — confirmado por la usuaria,
    así que alcanza con leer una (se prioriza BUY si ambas existen). No
    hay fallback a config manual: si esta columna no aparece en la
    grilla real, la fila queda con moneda vacía y se avisa (ver
    DISENO.md), en vez de asumir una moneda por proveedor."""
    price_code = price_code if price_code is not None else PRICE_CODE_DEFAULT
    _abrir_lista_rates(driver, codigo, etiqueta=f"lista {codigo}", price_code=price_code)

    periodos = _leer_periodos_rates(driver)
    if not periodos:
        dump(driver, f"rates_sin_periodos_{codigo[:10]}")
        tablas_vistas = _listar_tablas_pagina(driver)
        print(f"    ⚠ No encontré lista de períodos de RATES para {codigo}")
        print(f"      Tablas visibles en la página (headers): {tablas_vistas}")
        return []

    idxs = _periodos_en_rango(periodos, PERIODO_ANALISIS_DESDE, PERIODO_ANALISIS_HASTA)
    if not idxs:
        print(f"    ⚠ Ningún período de {codigo} cae dentro del rango configurado "
              f"(PERIODO_ANALISIS_DESDE/HASTA) — períodos vistos: "
              f"{[p['texto'] for p in periodos]}")
        return []
    print(f"    → {len(idxs)}/{len(periodos)} período(s) dentro del rango: "
          f"{[periodos[i]['texto'] for i in idxs]}")

    out = []
    for n, idx in enumerate(idxs):
        if n > 0:
            # Renavegar: el DOM de la lista se perdió al entrar al período anterior.
            _abrir_lista_rates(driver, codigo, etiqueta=f"lista {codigo} (período {n+1})",
                                price_code=price_code)
        periodo = periodos[idx]
        filas_periodo = driver.find_elements(By.CSS_SELECTOR, "td.tpcol-rateperiod")
        if idx >= len(filas_periodo):
            print(f"    ⚠ El período {periodo['texto']!r} de {codigo} ya no está en la "
                  f"posición esperada tras renavegar — se salta.")
            continue
        jc(driver, filas_periodo[idx])
        time.sleep(5 * VELOCIDAD)
        ss(driver, f"rates_periodo_{codigo[:10]}_{n+1}")
        if price_code:
            _seleccionar_price_code(driver, price_code, etiqueta=f"período {codigo}")

        tabla = _leer_tabla_rates(driver)
        for _ in range(3):
            if tabla:
                break
            time.sleep(2 * VELOCIDAD)
            tabla = _leer_tabla_rates(driver)
        if not tabla:
            dump(driver, f"rates_sin_tabla_{codigo[:10]}_{n+1}")
            tablas_vistas = _listar_tablas_pagina(driver)
            print(f"    ⚠ No encontré tabla de RATES (con columna COST) para {codigo} "
                  f"período {periodo['texto']!r}")
            print(f"      Tablas visibles en la página (headers): {tablas_vistas}")
            continue

        for fila in _extraer_filas_ad(tabla, codigo, moneda_periodo=periodo.get("moneda", "")):
            fila["periodo_desde"] = periodo["desde"].strftime("%d/%m/%Y") if periodo["desde"] else ""
            fila["periodo_hasta"] = periodo["hasta"].strftime("%d/%m/%Y") if periodo["hasta"] else ""
            fila["price_code"] = periodo["price_code"]
            out.append(fila)
    return out


# ── Cola de trabajo y moneda ─────────────────────────────────────────

def _validar_comparaciones(comparaciones):
    """Chequea que cada entrada de COMPARACIONES tenga lo mínimo
    indispensable ("location" y "generico", no vacíos) ANTES de abrir
    Chrome y loguear — un typo al editar/agregar una relación (ej. al
    descomentar el ejemplo de PEAPO/GUIAPO y olvidar la línea
    "generico") antes explotaba con un KeyError críptico en
    descubrir_cola, después de ya haber gastado una de las sesiones
    limitadas de licencia de Tourplan."""
    if not comparaciones:
        raise ValueError("COMPARACIONES está vacío — agregar al menos una relación "
                          "genérico/específico antes de correr.")
    for i, comp in enumerate(comparaciones):
        faltantes = [clave for clave in ("location", "generico") if not comp.get(clave)]
        if faltantes:
            raise ValueError(
                f"COMPARACIONES[{i}] ({comp!r}) no tiene {', '.join(faltantes)!s} "
                f"— revisar que la entrada esté completa (comparar contra el ejemplo "
                f"de TRFPO al principio del archivo).")


def descubrir_cola(driver, comparaciones, incluir_generico=True, incluir_especificos=True):
    """A partir de COMPARACIONES (una entrada por relación genérico/
    específico — location + supplier genérico + lista de específicos,
    ver COMPARACIONES), busca en Tourplan los códigos vigentes de cada
    supplier (listar_codigos_supplier) y devuelve la cola de trabajo
    completa — un (LOCATION, SUPPLIER, CODIGO, SERVICE_TYPE, ES_GENERICO,
    PRICE_CODE) por código encontrado. Requiere sesión ya logueada.

    incluir_generico/incluir_especificos permiten limitar qué se extrae
    esta corrida (ver MODO) — para no re-extraer el genérico completo
    cuando sólo hace falta actualizar un específico."""
    cola = []
    for comp in comparaciones:
        location = comp["location"]
        service_type = comp.get("service_type", "")
        price_code = comp.get("price_code", PRICE_CODE_DEFAULT)
        especificos = comp.get("especificos", comp.get("transportistas", []))
        suppliers = []
        if incluir_generico:
            suppliers.append((comp["generico"], True))
        if incluir_especificos:
            suppliers += [(s, False) for s in especificos]
        for supplier, es_generico in suppliers:
            items = listar_codigos_supplier(driver, location, supplier, service_type)
            for item in items:
                if _es_codigo_excluido(item["codigo"], item.get("descripcion", "")):
                    print(f"    (excluido, no es un servicio real: {item['codigo']} "
                          f"— {item.get('descripcion', '')!r})")
                    continue
                cola.append({
                    "location": location,
                    "supplier": supplier,
                    "codigo": item["codigo"],
                    "descripcion": item.get("descripcion", ""),
                    "service_type": service_type,
                    "es_generico": es_generico,
                    "generico": comp["generico"],
                    "price_code": price_code,
                })
    return cola


COLS_TARIFAS = ["SUPPLIER", "PRODUCT_CODE", "ES_GENERICO", "GENERICO", "DESCRIPCION",
                "PERIODO_DESDE", "PERIODO_HASTA", "PRICE_CODE", "PAX_DESDE", "PAX_HASTA",
                "TARIFA_VIGENTE", "MONEDA", "TARIFA_USD", "LOCATION", "TIMESTAMP"]
# GENERICO: a qué supplier genérico corresponde esta relación (para
# una fila ES_GENERICO=True, es su propio SUPPLIER; para una fila
# específica, el "generico" de la entrada de COMPARACIONES de la que
# vino) — necesario para no mezclar relaciones distintas que comparten
# location (ej. TRFPO y PEAPO, ambos en BUE) al armar la comparación.
# DESCRIPCION: la Description del código tal como aparece en el listado
# de Product Search (ya se leía en descubrir_cola, sólo faltaba
# guardarla) — necesaria para clasificar categoría de servicio
# (EXCURSION/TRASLADO/JAPON/CRUCEROS_*) y GUIA/SIN_GUIA de cada código
# específico, para el matching por vehículo en construir_comparacion_gap
# (ver DISENO.md "Bug real: matching por vehículo, no sólo por pax
# bruto").



def logout(driver):
    """Cierra ventanas secundarias y hace logout real. El logout solo se
    considera OK si después del click aparece de nuevo el formulario de
    login (input password) o la URL vuelve a #/login — encontrar el
    texto "logged in as" NO es prueba de logout, significa que la sesión
    sigue abierta. Portado de tp-nx-app
    (scripts/valorizacion_pkg/tourplan_valorizacion_pkg_v3.py::logout) —
    este script no lo tenía, y Tourplan tiene licencias concurrentes
    limitadas."""
    print("\n🔒 Finalización: cerrando ventanas y haciendo logout...")
    try:
        principal = driver.window_handles[0]
        for h in driver.window_handles[1:]:
            try:
                driver.switch_to.window(h); driver.close()
            except Exception: pass
        driver.switch_to.window(principal)
    except Exception: pass

    def _click_item(regex):
        return driver.execute_script("""
            var rx = new RegExp(arguments[0], 'i');
            var els = Array.from(document.querySelectorAll(
                'li, label, a, button, span, div'));
            var best = null;
            for (var el of els){
                if (!el.offsetParent) continue;
                var t = (el.innerText || '').trim();
                if (!t || t.length > 40 || !rx.test(t)) continue;
                if (!best || t.length <= (best.innerText||'').trim().length)
                    best = el;
            }
            if (!best) return null;
            best.click();
            return (best.innerText || '').trim().slice(0, 40);
        """, regex)

    def _en_login():
        return driver.execute_script("""
            var pwd = document.querySelector("input[type='password']");
            return (pwd && pwd.offsetParent !== null) ||
                   /login/i.test(window.location.href);
        """)

    try:
        driver.get(f"{BASE_URL}/#/home")
        time.sleep(4 * VELOCIDAD)
        ss(driver, "logout_home")

        clicked = None
        try:
            btn_panel = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#openUserPanel")))
            jc(driver, btn_panel)
            time.sleep(2 * VELOCIDAD)
            ss(driver, "logout_usermenu")
            btn_logout = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "div.panelHeader tp-button button, div.panelHeader button")))
            texto_btn = (btn_logout.text or "Logout").strip()
            jc(driver, btn_logout)
            clicked = texto_btn or "Logout"
        except Exception as _e:
            print(f"    ⚠ Flujo #openUserPanel falló ({_e}) — fallback por texto")

        rx_logout = r"^(log\s?out|sign\s?out|cerrar sesi)"
        if not clicked:
            clicked = _click_item(rx_logout)
        if not clicked:
            padre = _click_item(r"logged in as")
            time.sleep(2 * VELOCIDAD)
            ss(driver, "logout_submenu")
            if padre:
                clicked = _click_item(rx_logout)

        if not clicked:
            candidatos = driver.execute_script("""
                var out = [];
                for (var el of document.querySelectorAll('*')){
                    if (!el.offsetParent || el.children.length > 0) continue;
                    var t = (el.innerText || '').trim();
                    if (t && t.length < 40 && /log|user|usuario/i.test(t)) out.push(t);
                }
                return out.slice(0, 25);
            """)
            print(f"      Textos visibles con 'log/user': {candidatos}")

        time.sleep(5 * VELOCIDAD)
        ss(driver, "logout_done")

        if clicked and _en_login():
            print(f"  🔓 Logout OK (click en '{clicked}', volvió al login)")
        elif clicked:
            print(f"  ⚠ Click en '{clicked}' pero NO volvió al login — logout no confirmado")
        else:
            print("  ⚠ No encontré la opción LOG OUT en el menú — logout no realizado")
    except Exception as e:
        print(f"  ⚠ Error en logout: {e}")


def convertir_a_usd(tarifa, moneda, tipo_cambio):
    """None si no se puede convertir con confianza: falta la tarifa,
    la moneda vino vacía (columna CURRENCY no encontrada), la moneda no
    es ARS ni USD, o es ARS pero no hay tipo de cambio cargado — mejor
    dejar el dato en blanco para revisión manual que inventar un
    valor."""
    if tarifa is None or not moneda:
        return None
    if moneda == "USD":
        return round(tarifa, 2)
    if moneda == "ARS":
        return round(tarifa / tipo_cambio, 2) if tipo_cambio else None
    return None


# ── Fase 3 embebida: comparación de gap (versión en Python puro de
# comparacion_gap.py — sin pandas, para que este script siga siendo
# autocontenido en un solo archivo/una sola corrida de Colab. Misma
# lógica que ese archivo standalone; si se cambia una, replicar en la
# otra. Ver DISENO.md) ──────────────────────────────────────────────

COLS_GAP = [
    "LOCATION", "CODIGO_GENERICO", "SUPPLIER_ESPECIFICO", "CODIGO_ESPECIFICO",
    "PAX_DESDE", "PAX_HASTA",
    "PERIODO_ESPECIFICO_DESDE", "PERIODO_ESPECIFICO_HASTA",
    "PERIODO_GENERICO_DESDE", "PERIODO_GENERICO_HASTA",
    "TARIFA_GENERICO_USD", "TARIFA_ESPECIFICO_USD",
    "DIFERENCIA_USD", "DIFERENCIA_PCT", "FLAGS",
]


# ── Vehículo por sufijo de código + tabla de bases pax/vehículo (para
# elegir el tramo de pax de TRFPO que realmente le corresponde a cada
# código específico, en vez del PAX_DESDE/HASTA que reportó el
# específico en su propia tarifa — ver DISENO.md "Bug real: matching
# por vehículo, no sólo por pax bruto") ──────────────────────────────

# Sufijo de código -> nombre de vehículo (DISENO.md "Corrección a
# BRIEF.md": los sufijos SÍ están estandarizados, mismo significado
# siempre, independiente del transportista). V9 (Van 9 pax) es un
# vehículo real de algunos transportistas, pero TRFPO no lo valoriza
# como tramo propio (confirmado por la usuaria) — se excluye de la
# comparación en vez de forzar un match contra un tramo que no le
# corresponde.
SUFIJO_VEHICULO = {
    "AU": "Auto",
    "MV": "H1/VITO",
    "15": "Sprinter 15 pax",
    "19": "Sprinter 19 pax",
    "24": "Minibus",
    "37": "Bus 37 asientos",
    "42": "Bus",
}
SUFIJOS_VEHICULO_NO_VALORIZADOS_TRFPO = {"V9"}

# LOCATION/CATEGORIA/VEHICULO/GUIA -> rango de pax REAL de ese vehículo
# (no el PAX_DESDE/HASTA que reportó el específico en su propia
# tarifa, que puede ser un tramo distinto/más ancho). Copiado de
# config/tabla_bases_vehiculo_pax.csv — embebido acá en vez de leído
# como archivo aparte porque este script corre como celda única de
# Colab, sin archivos hermanos disponibles. Mantener sincronizado a
# mano con ese CSV (usado también por matching_engine.py/
# test_matching.py, Fase 2 offline). Sólo BUE está confirmado con
# corridas reales — FTE/USH están cargados igual que en el CSV pero
# sin validar contra Tourplan.
TABLA_BASES_VEHICULO_PAX = [
    {"location": "BUE", "categoria": "EXCURSION", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "BUE", "categoria": "EXCURSION", "vehiculo": "Auto", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "BUE", "categoria": "EXCURSION", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "BUE", "categoria": "EXCURSION", "vehiculo": "H1/VITO", "guia": "SIN_GUIA", "pax_desde": 3, "pax_hasta": 5},
    {"location": "BUE", "categoria": "EXCURSION", "vehiculo": "Sprinter 15 pax", "guia": "CON_GUIA", "pax_desde": 5, "pax_hasta": 11},
    {"location": "BUE", "categoria": "EXCURSION", "vehiculo": "Sprinter 15 pax", "guia": "SIN_GUIA", "pax_desde": 6, "pax_hasta": 11},
    {"location": "BUE", "categoria": "EXCURSION", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 12, "pax_hasta": 13},
    {"location": "BUE", "categoria": "EXCURSION", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 14, "pax_hasta": 19},
    {"location": "BUE", "categoria": "EXCURSION", "vehiculo": "Bus 37 asientos", "guia": "CON_GUIA", "pax_desde": 20, "pax_hasta": 33},
    {"location": "BUE", "categoria": "EXCURSION", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 34, "pax_hasta": 41},
    {"location": "BUE", "categoria": "TRASLADO", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "BUE", "categoria": "TRASLADO", "vehiculo": "Auto", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "BUE", "categoria": "TRASLADO", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "BUE", "categoria": "TRASLADO", "vehiculo": "H1/VITO", "guia": "SIN_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "BUE", "categoria": "TRASLADO", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 5, "pax_hasta": 11},
    {"location": "BUE", "categoria": "TRASLADO", "vehiculo": "Sprinter 19 pax", "guia": "SIN_GUIA", "pax_desde": 5, "pax_hasta": 11},
    {"location": "BUE", "categoria": "TRASLADO", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 12, "pax_hasta": 15},
    {"location": "BUE", "categoria": "TRASLADO", "vehiculo": "Bus 37 asientos", "guia": "CON_GUIA", "pax_desde": 16, "pax_hasta": 22},
    {"location": "BUE", "categoria": "TRASLADO", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 23, "pax_hasta": 35},
    {"location": "BUE", "categoria": "TRASLADO", "vehiculo": "Bus + VH para equipaje", "guia": "CON_GUIA", "pax_desde": 36, "pax_hasta": 41},
    {"location": "BUE", "categoria": "JAPON", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 1},
    {"location": "BUE", "categoria": "JAPON", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 2, "pax_hasta": 3},
    {"location": "BUE", "categoria": "JAPON", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 4, "pax_hasta": 5},
    {"location": "BUE", "categoria": "JAPON", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 6, "pax_hasta": 14},
    {"location": "BUE", "categoria": "JAPON", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 15, "pax_hasta": 38},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 1},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "Auto", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 1},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 2, "pax_hasta": 3},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "H1/VITO", "guia": "SIN_GUIA", "pax_desde": 2, "pax_hasta": 3},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "Sprinter 15 pax", "guia": "CON_GUIA", "pax_desde": 4, "pax_hasta": 6},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "Sprinter 15 pax", "guia": "SIN_GUIA", "pax_desde": 4, "pax_hasta": 6},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 7, "pax_hasta": 9},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "Sprinter 19 pax", "guia": "SIN_GUIA", "pax_desde": 7, "pax_hasta": 9},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 10, "pax_hasta": 13},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "Minibus", "guia": "SIN_GUIA", "pax_desde": 10, "pax_hasta": 11},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 14, "pax_hasta": 25},
    {"location": "BUE", "categoria": "CRUCEROS_EXCURSION", "vehiculo": "Bus + Van para equipaje", "guia": "CON_GUIA", "pax_desde": 26, "pax_hasta": 41},
    {"location": "BUE", "categoria": "CRUCEROS_TRASLADO", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 1},
    {"location": "BUE", "categoria": "CRUCEROS_TRASLADO", "vehiculo": "Auto", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 1},
    {"location": "BUE", "categoria": "CRUCEROS_TRASLADO", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 2, "pax_hasta": 3},
    {"location": "BUE", "categoria": "CRUCEROS_TRASLADO", "vehiculo": "H1/VITO", "guia": "SIN_GUIA", "pax_desde": 2, "pax_hasta": 3},
    {"location": "BUE", "categoria": "CRUCEROS_TRASLADO", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 4, "pax_hasta": 9},
    {"location": "BUE", "categoria": "CRUCEROS_TRASLADO", "vehiculo": "Sprinter 19 pax", "guia": "SIN_GUIA", "pax_desde": 4, "pax_hasta": 9},
    {"location": "BUE", "categoria": "CRUCEROS_TRASLADO", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 10, "pax_hasta": 13},
    {"location": "BUE", "categoria": "CRUCEROS_TRASLADO", "vehiculo": "Minibus", "guia": "SIN_GUIA", "pax_desde": 10, "pax_hasta": 11},
    {"location": "BUE", "categoria": "CRUCEROS_TRASLADO", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 14, "pax_hasta": 25},
    {"location": "BUE", "categoria": "CRUCEROS_TRASLADO", "vehiculo": "Bus + VH para equipaje", "guia": "CON_GUIA", "pax_desde": 26, "pax_hasta": 41},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Auto", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Doble Traccion (DT)", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Doble Traccion (DT)", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "H1/VITO", "guia": "SIN_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Sprinter 15 pax", "guia": "CON_GUIA", "pax_desde": 5, "pax_hasta": 11},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Sprinter 15 pax", "guia": "SIN_GUIA", "pax_desde": 5, "pax_hasta": 11},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 12, "pax_hasta": 13},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Sprinter 19 pax", "guia": "SIN_GUIA", "pax_desde": 12, "pax_hasta": 13},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 14, "pax_hasta": 19},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Minibus", "guia": "SIN_GUIA", "pax_desde": 14, "pax_hasta": 19},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 20, "pax_hasta": 41},
    {"location": "FTE", "categoria": "EXCURSION", "vehiculo": "Bus", "guia": "SIN_GUIA", "pax_desde": 20, "pax_hasta": 41},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Auto", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Doble Traccion (DT)", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Doble Traccion (DT)", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "H1/VITO", "guia": "SIN_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Sprinter 15 pax", "guia": "CON_GUIA", "pax_desde": 5, "pax_hasta": 6},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Sprinter 15 pax", "guia": "SIN_GUIA", "pax_desde": 5, "pax_hasta": 6},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 7, "pax_hasta": 11},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Sprinter 19 pax", "guia": "SIN_GUIA", "pax_desde": 7, "pax_hasta": 11},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 12, "pax_hasta": 15},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Minibus", "guia": "SIN_GUIA", "pax_desde": 12, "pax_hasta": 15},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 16, "pax_hasta": 41},
    {"location": "FTE", "categoria": "TRASLADO", "vehiculo": "Bus", "guia": "SIN_GUIA", "pax_desde": 16, "pax_hasta": 41},
    {"location": "FTE", "categoria": "JAPON", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "FTE", "categoria": "JAPON", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 3, "pax_hasta": 3},
    {"location": "FTE", "categoria": "JAPON", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 4, "pax_hasta": 5},
    {"location": "FTE", "categoria": "JAPON", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 6, "pax_hasta": 14},
    {"location": "FTE", "categoria": "JAPON", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 15, "pax_hasta": 38},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Auto", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Doble Traccion (DT)", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Doble Traccion (DT)", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "H1/VITO", "guia": "SIN_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Hi Ace/Van 12 pax", "guia": "CON_GUIA", "pax_desde": 5, "pax_hasta": 8},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Hi Ace/Van 12 pax", "guia": "SIN_GUIA", "pax_desde": 5, "pax_hasta": 8},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Sprinter 15 pax", "guia": "CON_GUIA", "pax_desde": 5, "pax_hasta": 11},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Sprinter 15 pax", "guia": "SIN_GUIA", "pax_desde": 5, "pax_hasta": 11},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 12, "pax_hasta": 13},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Sprinter 19 pax", "guia": "SIN_GUIA", "pax_desde": 12, "pax_hasta": 13},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 14, "pax_hasta": 19},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Minibus", "guia": "SIN_GUIA", "pax_desde": 14, "pax_hasta": 19},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 20, "pax_hasta": 42},
    {"location": "USH", "categoria": "EXCURSION", "vehiculo": "Bus", "guia": "SIN_GUIA", "pax_desde": 20, "pax_hasta": 42},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Auto", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Doble Traccion (DT)", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Doble Traccion (DT)", "guia": "SIN_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "H1/VITO", "guia": "SIN_GUIA", "pax_desde": 3, "pax_hasta": 4},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Hi Ace/Van 12 pax", "guia": "CON_GUIA", "pax_desde": 5, "pax_hasta": 6},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Hi Ace/Van 12 pax", "guia": "SIN_GUIA", "pax_desde": 5, "pax_hasta": 6},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Sprinter 15 pax", "guia": "CON_GUIA", "pax_desde": 5, "pax_hasta": 6},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Sprinter 15 pax", "guia": "SIN_GUIA", "pax_desde": 5, "pax_hasta": 6},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 7, "pax_hasta": 11},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Sprinter 19 pax", "guia": "SIN_GUIA", "pax_desde": 7, "pax_hasta": 11},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 12, "pax_hasta": 15},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Minibus", "guia": "SIN_GUIA", "pax_desde": 12, "pax_hasta": 15},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 16, "pax_hasta": 41},
    {"location": "USH", "categoria": "TRASLADO", "vehiculo": "Bus", "guia": "SIN_GUIA", "pax_desde": 16, "pax_hasta": 41},
    {"location": "USH", "categoria": "JAPON", "vehiculo": "Auto", "guia": "CON_GUIA", "pax_desde": 1, "pax_hasta": 2},
    {"location": "USH", "categoria": "JAPON", "vehiculo": "H1/VITO", "guia": "CON_GUIA", "pax_desde": 3, "pax_hasta": 3},
    {"location": "USH", "categoria": "JAPON", "vehiculo": "Sprinter 19 pax", "guia": "CON_GUIA", "pax_desde": 4, "pax_hasta": 5},
    {"location": "USH", "categoria": "JAPON", "vehiculo": "Minibus", "guia": "CON_GUIA", "pax_desde": 6, "pax_hasta": 14},
    {"location": "USH", "categoria": "JAPON", "vehiculo": "Bus", "guia": "CON_GUIA", "pax_desde": 15, "pax_hasta": 38},
]


def _vehiculo_por_sufijo(codigo):
    """Devuelve (vehiculo, sufijo). vehiculo es None si el sufijo no
    está contemplado en SUFIJO_VEHICULO (código con un sufijo que
    todavía no se mapeó), o "NO_VALORIZADO" si es un vehículo real que
    TRFPO no valoriza como tramo propio (ver
    SUFIJOS_VEHICULO_NO_VALORIZADOS_TRFPO)."""
    sufijo = str(codigo or "")[-2:].upper()
    if sufijo in SUFIJOS_VEHICULO_NO_VALORIZADOS_TRFPO:
        return "NO_VALORIZADO", sufijo
    return SUFIJO_VEHICULO.get(sufijo), sufijo


# Copia reducida de matching_engine.py (detectar_flags/
# clasificar_categoria/clasificar_guia, Fase 2) para no depender de
# importar ese archivo como módulo aparte — este script corre como
# celda única de Colab, sin archivos hermanos disponibles. Si se ajusta
# la lógica de clasificación en matching_engine.py, replicar acá
# también.
_EXCURSION_PATTERNS_ESPECIFICO = [
    re.compile(r'CITY', re.I),
    re.compile(r'DINNER\s*SHOW', re.I),
    re.compile(r'\bFD\s', re.I),
    re.compile(r'HD\s*TIGRE', re.I),
    re.compile(r'SOLO\s*SHOW', re.I),
    re.compile(r'HRS?\s*DISPO', re.I),
    re.compile(r'HR\s*ESPERA', re.I),
    re.compile(r'MEETING\s*POINT', re.I),
    re.compile(r'DISPO\s*GUIA', re.I),
]


def _clasificar_categoria_guia_especifico(codigo, descripcion):
    """Devuelve (categoria, guia) para un código específico — misma
    lógica que matching_engine.py::clasificar_categoria/clasificar_guia
    (Fase 2): JAPON/CRUCERO por keyword de Description o prefijo de
    código, EXCURSION por keyword de Description (si no matchea
    ninguna, TRASLADO por default), SIN_GUIA por keyword o código que
    empieza con "N". JAPON siempre usa CON_GUIA como clave de lookup
    (tabla_bases_vehiculo_pax.csv no distingue guía para esa
    categoría)."""
    codigo = str(codigo or "")
    desc = descripcion or ""
    es_japon = codigo.upper().startswith("J") or "japon" in desc.lower()
    es_crucero = "crucero" in desc.lower()
    es_sin_guia = "s/guia" in desc.lower() or codigo.upper().startswith("N")
    es_excursion = any(p.search(desc) for p in _EXCURSION_PATTERNS_ESPECIFICO)

    if es_japon:
        categoria = "JAPON"
    elif es_crucero:
        categoria = "CRUCEROS_EXCURSION" if es_excursion else "CRUCEROS_TRASLADO"
    else:
        categoria = "EXCURSION" if es_excursion else "TRASLADO"

    guia = "CON_GUIA" if categoria == "JAPON" else ("SIN_GUIA" if es_sin_guia else "CON_GUIA")
    return categoria, guia


def _pax_esperado_vehiculo(location, categoria, vehiculo, guia):
    """Busca en TABLA_BASES_VEHICULO_PAX el rango de pax real de un
    vehículo para esa location/categoría/guía. Si no hay fila para esa
    guía puntual (algunas categorías/vehículos sólo tienen CON_GUIA
    cargado) cae a buscar sin filtrar por guía. Devuelve
    (pax_desde, pax_hasta) o None si no encuentra ninguna fila."""
    candidatas = [r for r in TABLA_BASES_VEHICULO_PAX
                  if r["location"] == location and r["categoria"] == categoria
                  and r["vehiculo"] == vehiculo and r["guia"] == guia]
    if not candidatas:
        candidatas = [r for r in TABLA_BASES_VEHICULO_PAX
                      if r["location"] == location and r["categoria"] == categoria
                      and r["vehiculo"] == vehiculo]
    if not candidatas:
        return None
    r = candidatas[0]
    return r["pax_desde"], r["pax_hasta"]


def _pax_overlap(desde1, hasta1, desde2, hasta2):
    """Cantidad de pax en común entre dos rangos — análogo a
    _dias_superposicion pero para rangos de pax en vez de fechas."""
    if desde1 is None or hasta1 is None or desde2 is None or hasta2 is None:
        return 0
    return max(0, min(hasta1, hasta2) - max(desde1, desde2) + 1)


def _mejor_prefijo_generico(codigo, codigos_generico_desc):
    """Longest-prefix-match código específico → código genérico. Mismo
    algoritmo que matching_engine.py::mejor_prefijo_trfpo (Fase 2) —
    nunca dependió de nada específico de TRFPO (es prefijo de string
    puro), así que sirve igual para cualquier otra relación genérico/
    específico (PEAPO, GUIAPO, la que sea) sin cambios."""
    candidatos = [c for c in codigos_generico_desc if codigo.startswith(c)]
    if not candidatos:
        return None, []
    return max(candidatos, key=len), candidatos


def _dias_superposicion(desde1, hasta1, desde2, hasta2):
    """Copiado de comparacion_gap.py::_dias_superposicion. Días de
    superposición entre dos rangos de fechas — 0 (no negativo) si no
    se superponen o si falta alguna fecha, para poder usar max() sin
    excepciones por None."""
    if not (desde1 and hasta1 and desde2 and hasta2):
        return 0
    inicio = max(desde1, desde2)
    fin = min(hasta1, hasta2)
    return max(0, (fin - inicio).days)


def construir_comparacion_gap(filas):
    """A partir de filas_salida (la misma lista de dicts que se
    escribe en tarifas_vigentes.xlsx, ya en memoria — no hace falta
    releer el Excel) calcula el gap genérico vs. cada código
    específico. Cada fila específica sólo se compara contra los
    genéricos de SU MISMA relación (columna `GENERICO`, ver
    COLS_TARIFAS) — no contra cualquier genérico que comparta su
    location — para no mezclar entre sí relaciones distintas que
    convivan en la misma location (ej. TRFPO y PEAPO ambos en BUE). Si
    a una fila específica le falta `GENERICO` (dato viejo, de antes de
    que existiera esta columna), cae a compararse contra TODOS los
    genéricos de su location, como se hacía antes. Ver comparacion_gap.py
    para el detalle de banderas y criterio de match (idéntico acá, sólo
    sin pandas)."""
    por_location = {}
    for f in filas:
        por_location.setdefault(f["LOCATION"], []).append(f)

    salida = []
    for location, filas_loc in por_location.items():
        filas_generico_todos = [f for f in filas_loc if f["ES_GENERICO"]]
        filas_especifico = [f for f in filas_loc if not f["ES_GENERICO"]]

        for fe in filas_especifico:
            generico_asociado = fe.get("GENERICO")
            filas_generico = (
                [f for f in filas_generico_todos if f["SUPPLIER"] == generico_asociado]
                if generico_asociado else filas_generico_todos
            )
            codigos_generico = sorted({f["PRODUCT_CODE"] for f in filas_generico}, key=len, reverse=True)

            base = {
                "LOCATION": location,
                "SUPPLIER_ESPECIFICO": fe["SUPPLIER"],
                "CODIGO_ESPECIFICO": fe["PRODUCT_CODE"],
                "PAX_DESDE": fe["PAX_DESDE"], "PAX_HASTA": fe["PAX_HASTA"],
                "PERIODO_ESPECIFICO_DESDE": fe["PERIODO_DESDE"],
                "PERIODO_ESPECIFICO_HASTA": fe["PERIODO_HASTA"],
                "PERIODO_GENERICO_DESDE": "", "PERIODO_GENERICO_HASTA": "",
                "TARIFA_ESPECIFICO_USD": fe["TARIFA_USD"],
            }
            flags = []

            mejor, candidatos = _mejor_prefijo_generico(fe["PRODUCT_CODE"], codigos_generico)
            if len(candidatos) > 1:
                flags.append("COLISION_REVISAR")

            vehiculo, sufijo = _vehiculo_por_sufijo(fe["PRODUCT_CODE"])
            # Código específico IDÉNTICO al genérico (sin sufijo de vehículo
            # — ej. peajes PJ: AE, CX, D120): comparten los mismos pax breaks,
            # así que se compara tramo contra tramo con el rango de pax del
            # propio específico, sin pasar por la tabla de vehículos.
            sin_sufijo = mejor is not None and mejor == fe["PRODUCT_CODE"]
            if vehiculo == "NO_VALORIZADO" and not sin_sufijo:
                # Vehículo real (ej. V9/Van 9 pax) que TRFPO no valoriza
                # como tramo propio — no hay ningún tramo de TRFPO
                # contra el cual comparar, así que se excluye en vez de
                # forzar un match contra un tramo que no le corresponde
                # (ver DISENO.md).
                flags.append(f"VEHICULO_{sufijo}_NO_VALORIZADO_TRFPO")
                salida.append({**base, "CODIGO_GENERICO": mejor or "", "TARIFA_GENERICO_USD": None,
                               "DIFERENCIA_USD": None, "DIFERENCIA_PCT": None,
                               "FLAGS": ",".join(flags)})
                continue

            if mejor is None:
                salida.append({**base, "CODIGO_GENERICO": "", "TARIFA_GENERICO_USD": None,
                               "DIFERENCIA_USD": None, "DIFERENCIA_PCT": None,
                               "FLAGS": ",".join(flags + ["SIN_MATCH_GENERICO"])})
                continue

            # Rango de pax a usar para elegir el tramo correcto de
            # TRFPO: el "real" del vehículo según
            # TABLA_BASES_VEHICULO_PAX (por sufijo de código +
            # categoría/guía clasificadas de la Description), no el
            # PAX_DESDE/HASTA que reportó el específico en su propia
            # tarifa — puede ser un tramo más ancho o distinto del que
            # realmente le corresponde a ese vehículo (bug real
            # encontrado por la usuaria, ver DISENO.md).
            pax_desde_cmp, pax_hasta_cmp = fe["PAX_DESDE"], fe["PAX_HASTA"]
            if sin_sufijo:
                pass  # usa el rango de pax del propio específico
            elif vehiculo is None:
                flags.append(f"SUFIJO_VEHICULO_DESCONOCIDO_{sufijo}")
            else:
                categoria, guia = _clasificar_categoria_guia_especifico(
                    fe["PRODUCT_CODE"], fe.get("DESCRIPCION", ""))
                esperado = _pax_esperado_vehiculo(location, categoria, vehiculo, guia)
                if esperado is None:
                    flags.append(f"SIN_BASE_VEHICULO_PAX_{categoria}_{vehiculo}".replace(" ", "_"))
                else:
                    pax_desde_cmp, pax_hasta_cmp = esperado

            candidatas = [
                f for f in filas_generico
                if f["PRODUCT_CODE"] == mejor
                and f["PAX_DESDE"] is not None and f["PAX_HASTA"] is not None
                and pax_desde_cmp is not None and pax_hasta_cmp is not None
                and f["PAX_DESDE"] <= pax_hasta_cmp and f["PAX_HASTA"] >= pax_desde_cmp
            ]
            if not candidatas:
                salida.append({**base, "CODIGO_GENERICO": mejor, "TARIFA_GENERICO_USD": None,
                               "DIFERENCIA_USD": None, "DIFERENCIA_PCT": None,
                               "FLAGS": ",".join(flags + ["SIN_GENERICO_PARA_ESE_PAX"])})
                continue

            # Más de un tramo de TRFPO candidato (ej. dos períodos
            # solapados, o el rango de pax esperado del vehículo cae a
            # caballo entre dos tramos de TRFPO): se elige primero por
            # MAYOR superposición de pax con el rango esperado del
            # vehículo, y ante empate por MAYOR superposición de fechas
            # con el período del específico. Se marca con
            # MULTIPLES_PERIODOS_GENERICO para no descartar el resto en
            # silencio — el/la que revise sabe que hay otro(s)
            # tramo(s)/período(s) del genérico en tarifas_vigentes.xlsx
            # para ese mismo código.
            if len(candidatas) > 1:
                flags.append("MULTIPLES_PERIODOS_GENERICO")

            def _dias(f):
                return _dias_superposicion(
                    parsear_fecha(fe["PERIODO_DESDE"]), parsear_fecha(fe["PERIODO_HASTA"]),
                    parsear_fecha(f["PERIODO_DESDE"]), parsear_fecha(f["PERIODO_HASTA"]))

            def _pax_ov(f):
                return _pax_overlap(pax_desde_cmp, pax_hasta_cmp, f["PAX_DESDE"], f["PAX_HASTA"])

            fila_generico = max(candidatas, key=lambda f: (_pax_ov(f), _dias(f)))
            if _dias(fila_generico) == 0:
                flags.append("SIN_SUPERPOSICION_DE_PERIODO")

            tarifa_generico = fila_generico["TARIFA_USD"]
            tarifa_especifico = fe["TARIFA_USD"]
            diff_usd = diff_pct = None
            if tarifa_generico is not None and tarifa_especifico is not None:
                diff_usd = round(tarifa_especifico - tarifa_generico, 2)
                if tarifa_generico:
                    diff_pct = round((tarifa_especifico / tarifa_generico - 1) * 100, 2)
            else:
                flags.append("SIN_TARIFA_USD_PARA_COMPARAR")

            salida.append({**base, "CODIGO_GENERICO": mejor,
                           "PERIODO_GENERICO_DESDE": fila_generico["PERIODO_DESDE"],
                           "PERIODO_GENERICO_HASTA": fila_generico["PERIODO_HASTA"],
                           "TARIFA_GENERICO_USD": tarifa_generico,
                           "DIFERENCIA_USD": diff_usd, "DIFERENCIA_PCT": diff_pct,
                           "FLAGS": ",".join(flags)})
    return salida


# ── Google Sheet como almacén ────────────────────────────────────────
COLS_COMPARACION = COLS_GAP + ["ACTUALIZADO"]
_COLS_NUM_FLOAT = ("TARIFA_VIGENTE", "TARIFA_USD")
_COLS_NUM_INT = ("PAX_DESDE", "PAX_HASTA")


def abrir_spreadsheet():
    if not SHEET_URL:
        raise ValueError("Falta TOURPLAN_SHEET_URL (URL del Google Sheet de salida, "
                         "se carga en ⚙️ Configuración de la app).")
    creds = _cargar_credenciales(CREDENTIALS_PATH, TOKEN_PATH)
    return gspread.authorize(creds).open_by_url(SHEET_URL)


def obtener_hoja(sh, titulo, columnas):
    """Devuelve la pestaña `titulo` (la crea si no existe) con `columnas`
    como encabezado. Si ya existía con otro encabezado, no la toca —
    lee por nombre de columna, así que sólo importa que estén todas."""
    for ws in sh.worksheets():
        if ws.title.strip().lower() == titulo.lower():
            return ws
    ws = sh.add_worksheet(title=titulo, rows=2, cols=len(columnas))
    ws.update(range_name="A1", values=[columnas], value_input_option="RAW")
    return ws


def _a_numero(v, entero=False):
    if v is None or v == "":
        return None
    try:
        n = float(str(v).replace(",", ".")) if isinstance(v, str) else float(v)
    except ValueError:
        return None
    return int(round(n)) if entero else n


def _a_bool(v):
    return v is True or str(v).strip().upper() in ("TRUE", "SI", "SÍ", "1")


def normalizar_fila_tarifa(fila):
    """Convierte los tipos de una fila leída del Sheet a los que usa
    construir_comparacion_gap (mismos que tenían en memoria al extraer)."""
    f = dict(fila)
    f["ES_GENERICO"] = _a_bool(f.get("ES_GENERICO"))
    for c in _COLS_NUM_FLOAT:
        f[c] = _a_numero(f.get(c))
    for c in _COLS_NUM_INT:
        f[c] = _a_numero(f.get(c), entero=True)
    for c in ("PERIODO_DESDE", "PERIODO_HASTA", "SUPPLIER", "PRODUCT_CODE",
              "LOCATION", "GENERICO", "DESCRIPCION", "PRICE_CODE", "MONEDA"):
        f[c] = "" if f.get(c) is None else str(f[c])
    return f


def leer_filas(ws, normalizar=None):
    """Filas de la pestaña como dicts. UNFORMATTED_VALUE: números siempre
    como números, sin depender del idioma/formato regional del Sheet."""
    valores = ws.get_all_values(value_render_option="UNFORMATTED_VALUE")
    if len(valores) < 2:
        return []
    header = valores[0]
    filas = [dict(zip(header, r)) for r in valores[1:] if any(str(c).strip() for c in r)]
    return [normalizar(f) for f in filas] if normalizar else filas


def reescribir_hoja(ws, columnas, filas, formato_pct_col=None):
    """Reemplaza el contenido de la pestaña (encabezado + filas)."""
    def celda(v):
        return "" if v is None else v

    valores = [columnas] + [[celda(f.get(c)) for c in columnas] for f in filas]
    ws.clear()
    ws.resize(rows=max(len(valores), 2), cols=len(columnas))
    for i in range(0, len(valores), 2000):
        ws.update(range_name=f"A{i + 1}", values=valores[i:i + 2000], value_input_option="RAW")
    ws.format("1:1", {"textFormat": {"bold": True}})
    if formato_pct_col and filas:
        col = columnas.index(formato_pct_col) + 1
        rango = gspread.utils.rowcol_to_a1(2, col) + ":" + gspread.utils.rowcol_to_a1(len(valores), col)
        ws.format(rango, {"numberFormat": {"type": "NUMBER", "pattern": '0.00"%"'}})


def mezclar_tarifas(existentes, nuevas, reemplazar_grupos, reemplazar_codigos):
    """Pisa en `existentes` lo que esta corrida volvió a extraer y agrega
    `nuevas`. Un grupo es (SUPPLIER, LOCATION); un código es
    (SUPPLIER, LOCATION, PRODUCT_CODE). Lo que no esté en ninguno de los
    dos conjuntos queda intacto."""
    def conserva(f):
        g = (f["SUPPLIER"], f["LOCATION"])
        return g not in reemplazar_grupos and (g + (f["PRODUCT_CODE"],)) not in reemplazar_codigos
    return [f for f in existentes if conserva(f)] + list(nuevas)


def recalcular_comparacion(generico_rows, especifico_rows, locations):
    """Compara TODOS los específicos archivados de `locations` contra los
    genéricos archivados de esas locations (ver construir_comparacion_gap)."""
    locs = set(locations)
    filas = ([f for f in generico_rows if f["LOCATION"] in locs]
             + [f for f in especifico_rows if f["LOCATION"] in locs])
    return construir_comparacion_gap(filas)


def borrar_filas_hoja(sh, ws, indices):
    """Borra de la pestaña las filas en las posiciones `indices` (0 = primera
    fila de datos, o sea la fila 2 de la hoja) con UNA sola llamada, de abajo
    hacia arriba para no correr los índices."""
    if not indices:
        return
    bloques, ini, prev = [], None, None
    for i in sorted(indices):
        if ini is None:
            ini = prev = i
        elif i == prev + 1:
            prev = i
        else:
            bloques.append((ini, prev))
            ini = prev = i
    bloques.append((ini, prev))
    reqs = [{"deleteDimension": {"range": {
        "sheetId": ws.id, "dimension": "ROWS",
        "startIndex": a + 1, "endIndex": b + 2}}} for a, b in reversed(bloques)]
    sh.batch_update({"requests": reqs})


def reemplazar_filas_codigo(sh, ws, columnas, destino, sup, loc, codigo, nuevas):
    """Guarda YA las filas de un código: borra las que había archivadas de
    ese (supplier, location, código) y agrega las nuevas al final. `destino`
    es la copia en memoria de la pestaña (mismo orden) y se mantiene al día."""
    viejos = [i for i, f in enumerate(destino)
              if f["SUPPLIER"] == sup and f["LOCATION"] == loc and f["PRODUCT_CODE"] == codigo]
    if viejos:
        borrar_filas_hoja(sh, ws, viejos)
        for i in reversed(viejos):
            del destino[i]
    if nuevas:
        ws.append_rows([["" if f.get(c) is None else f.get(c) for c in columnas] for f in nuevas],
                       value_input_option="RAW", table_range="A1")
        destino.extend(nuevas)


# ── Cola de trabajo según el modo ────────────────────────────────────
def armar_plan(modo, esp_registrados, gen_archivados):
    """Devuelve [(comparaciones, incluir_generico, incluir_especificos), ...]
    — cada tramo en el formato que espera descubrir_cola."""
    if modo in ("GENERICO", "COMPLETO"):
        return [([{"location": loc, "service_type": SERVICE_TYPE_GENERICO,
                   "generico": GENERICO,
                   "especificos": ESPECIFICOS if modo == "COMPLETO" else [],
                   "price_code": PRICE_CODE_DEFAULT} for loc in LOCATIONS],
                 True, modo == "COMPLETO")]
    if modo == "ESPECIFICO":
        # El genérico elegido (servicio) define contra cuál se compara: queda
        # asociado a las filas del específico y la comparación sólo mira ese.
        return [([{"location": loc, "service_type": "", "generico": GENERICO,
                   "especificos": ESPECIFICOS, "price_code": PRICE_CODE_DEFAULT}
                  for loc in LOCATIONS], False, True)]

    # ACTUALIZAR: sólo lo seleccionado (o todos los específicos registrados).
    sel = ACTUALIZAR_SELECCION
    pares_esp = {(f["SUPPLIER"], f["LOCATION"]): f["GENERICO"] for f in esp_registrados}
    pares_gen = {(f["SUPPLIER"], f["LOCATION"]) for f in gen_archivados}
    if sel:
        desconocidos = sorted(p for p in sel if p not in pares_esp and p not in pares_gen)
        for sup, loc in desconocidos:
            print(f"⚠ {sup}@{loc} no está archivado en {HOJA_GENERICOS} ni {HOJA_ESPECIFICOS} — se ignora.")
        pares_esp = {p: g for p, g in pares_esp.items() if p in sel}
        pares_gen = {p for p in pares_gen if p in sel and p not in pares_esp}
    else:
        pares_gen = set()
    grupos = {}
    for (sup, loc), gen in pares_esp.items():
        grupos.setdefault((loc, gen), set()).add(sup)
    comps_esp = [{"location": loc, "service_type": "", "generico": gen,
                  "especificos": sorted(sups), "price_code": PRICE_CODE_DEFAULT}
                 for (loc, gen), sups in sorted(grupos.items())]
    comps_gen = [{"location": loc, "service_type": "", "generico": sup,
                  "especificos": [], "price_code": PRICE_CODE_DEFAULT}
                 for sup, loc in sorted(pares_gen)]
    plan = []
    if comps_esp:
        plan.append((comps_esp, False, True))
    if comps_gen:
        plan.append((comps_gen, True, False))
    return plan


def validar_config(modo):
    if modo not in ("GENERICO", "ESPECIFICO", "COMPLETO", "ACTUALIZAR"):
        raise ValueError(f"TOURPLAN_MODO inválido: {modo!r} — usar GENERICO, "
                         f"ESPECIFICO, COMPLETO o ACTUALIZAR")
    if not (USERNAME and PASSWORD):
        raise ValueError("Faltan usuario/password de Tourplan (⚙️ Configuración de la app).")
    if modo != "ACTUALIZAR" and not LOCATIONS:
        raise ValueError("Elegí al menos una location (TOURPLAN_LOCATIONS).")
    if modo in ("GENERICO", "COMPLETO") and not GENERICO:
        raise ValueError("Falta el supplier genérico (TOURPLAN_GENERICO).")
    if modo == "ESPECIFICO" and not GENERICO:
        raise ValueError("Falta el genérico contra el que comparar (TOURPLAN_GENERICO).")
    if modo in ("ESPECIFICO", "COMPLETO") and not ESPECIFICOS:
        raise ValueError("Falta al menos un proveedor específico (TOURPLAN_ESPECIFICOS).")


# ── Main ──────────────────────────────────────────────────────────
# ── Navegación optimizada por la lupa de Product Search ──────────────
# Portada de Drive-TP-NX-App (extraccion_vigencias.py). Con 2+ códigos del
# mismo SUPPLIER, buscar_producto() resetea la página y retipea todos los
# filtros por CADA código. Atajo confirmado por la usuaria: estando
# parado en un producto de #/product, la lupa abre un popover con la
# lista de la búsqueda anterior, y ahí se elige el código siguiente.
# La primera fila del popover es SIEMPRE el producto actual (se descarta).
# Ante cualquier duda se cae a buscar_producto() (camino lento pero
# validado), nunca queda en un estado ambiguo.

def _cerrar_ultimo_tp_dialog(driver, max_espera=4):
    """Clickea EXIT/CANCEL/CLOSE en el ÚLTIMO <tp-dialog> visible y espera
    a que se cierre. Angular nunca saca del DOM un tp-dialog ya cerrado
    (sólo deja de mostrarlo), así que el diálogo activo es siempre el
    último. Mismo helper que Drive-TP-NX-App (valorizacion_desde_madre).
    Devuelve True si se cerró, False si seguía abierto."""
    try:
        driver.execute_script("""
            function vis(e){ return !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length); }
            var dlgs = document.querySelectorAll('tp-dialog');
            var dlg = dlgs.length ? dlgs[dlgs.length - 1] : document;
            var b = dlg.querySelector(
                'button.tpcancel, tp-button.cancel > button, tp-button.close > button, tp-button.exit > button');
            if (b && vis(b)) { b.click(); return; }
            var btns = Array.from(dlg.querySelectorAll('button')).filter(vis);
            for (var btn of btns) {
                var t = (btn.innerText || '').trim().toUpperCase();
                if (t === 'EXIT' || t === 'CANCEL' || t === 'CLOSE') { btn.click(); return; }
            }
        """)
        time.sleep(1)
    except Exception:
        pass
    for _ in range(max_espera):
        if not _hay_dialogo_visible(driver):
            return True
        time.sleep(1)
    return False


def _hay_dialogo_visible(driver):
    return bool(driver.execute_script("""
        var dlgs = document.querySelectorAll('tp-dialog');
        var dlg = dlgs.length ? dlgs[dlgs.length - 1] : null;
        return !!(dlg && dlg.offsetParent);
    """))


def cerrar_dialogos_abiertos(driver, max_dialogos=4):
    """Cierra con EXIT todo diálogo que haya quedado abierto (típicamente
    el detalle del período de RATES que se acaba de leer) para dejar la
    pantalla del producto libre antes de usar la lupa. Devuelve True si
    no quedó ninguno abierto."""
    for _ in range(max_dialogos):
        if not _hay_dialogo_visible(driver):
            return True
        if not _cerrar_ultimo_tp_dialog(driver):
            print("    ⚠ Un diálogo no se cerró con EXIT")
            return False
    return not _hay_dialogo_visible(driver)


def _en_contexto_producto(driver):
    """True si el driver quedó parado en un producto (menú con RATES)."""
    try:
        img = WebDriverWait(driver, 4).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "nav img")))
        jc(driver, img)
        time.sleep(2 * VELOCIDAD)
        items = driver.execute_script("""
            return Array.from(document.querySelectorAll('nav ul > li')).map(function(li){
                return (li.querySelector('div div')||li).innerText.trim().split('\\n')[0].toUpperCase();
            });
        """) or []
        ok = "RATES" in items
        jc(driver, img)
        time.sleep(1)
        return ok, items
    except Exception:
        return False, []


def _saltar_a_producto_via_lupa(driver, codigo):
    """True si encontró `codigo` (match exacto, nunca por posición) en el
    popover de la lupa, lo abrió y confirmó contexto de producto."""
    try:
        lupa = wait(driver, "#searchWrapper li:nth-of-type(2) button", t=4)
        jc(driver, lupa)
        time.sleep(2 * VELOCIDAD)
    except Exception:
        return False

    cod_upper = (codigo or "").strip().upper()

    def _click_si_esta():
        return driver.execute_script("""
            var cod = arguments[0];
            var dialogs = document.querySelectorAll('tp-dialog');
            if (!dialogs.length) return null;
            var vis = Array.from(dialogs).filter(function(d){ return d.offsetParent; });
            var dlg = vis.length ? vis[vis.length - 1] : (dialogs.length > 1 ? dialogs[1] : dialogs[0]);
            var rows = Array.from(dlg.querySelectorAll('tr')).slice(1);
            for (var tr of rows){
                var celda = tr.querySelector('td.tpcol-optioncode');
                if (celda && celda.innerText.trim().toUpperCase() === cod){
                    celda.click();
                    return celda.innerText.trim();
                }
            }
            return null;
        """, cod_upper)

    def _scroll_popover():
        return bool(driver.execute_script("""
            var dialogs = document.querySelectorAll('tp-dialog');
            if (!dialogs.length) return false;
            var vis = Array.from(dialogs).filter(function(d){ return d.offsetParent; });
            var dlg = vis.length ? vis[vis.length - 1] : (dialogs.length > 1 ? dialogs[1] : dialogs[0]);
            var fila = dlg.querySelector('tr');
            if (!fila) return false;
            var cur = fila.closest('table');
            while (cur && cur !== dlg){
                if (cur.scrollHeight > cur.clientHeight + 5){
                    var antes = cur.scrollTop;
                    cur.scrollTop = cur.scrollTop + cur.clientHeight;
                    return cur.scrollTop > antes;
                }
                cur = cur.parentElement;
            }
            return false;
        """))

    clicked = _click_si_esta()
    intentos = 0
    while not clicked and intentos < 40:
        if not _scroll_popover():
            break
        time.sleep(0.4 * VELOCIDAD)
        clicked = _click_si_esta()
        intentos += 1
    if not clicked:
        return False

    time.sleep(4 * VELOCIDAD)
    ok_ctx, _ = _en_contexto_producto(driver)
    return ok_ctx


def _abrir_primero_de_grupo(driver, supplier, codigo):
    """Abre el primer código de un grupo de 2+ del mismo SUPPLIER buscando
    SOLO por proveedor (sin location, service type ni código): deja en
    pantalla la lista más amplia posible para que los códigos siguientes
    —aunque sean de otra location— estén disponibles en el popover de la
    lupa. Escrollea de a poco buscando el código exacto."""
    codigo = str(codigo).strip()
    supplier = str(supplier).strip()
    print(f"\n  📦 Buscando (grupo, sólo por proveedor): {supplier} → {codigo}")
    _completar_filtros_busqueda(driver, "", supplier, "", "")
    cod_upper = codigo.upper()

    def _click_si_esta():
        return driver.execute_script("""
            var cod = arguments[0];
            var rows = Array.from(document.querySelectorAll('table tbody tr'));
            for (var tr of rows){
                var tds = Array.from(tr.querySelectorAll('td'));
                var hasCod = tds.some(function(td){
                    return td.children.length===0 && td.innerText.trim().toUpperCase()===cod;
                });
                if (hasCod){
                    var target = tds.length > 4 ? tds[4] : tds[tds.length-1];
                    target.click();
                    return target.innerText.trim().slice(0,50) || 'clicked';
                }
            }
            return null;
        """, cod_upper)

    clicked = _click_si_esta()
    intentos = 0
    while not clicked and intentos < 40:
        if not _hacer_scroll_resultados(driver):
            break
        time.sleep(0.4 * VELOCIDAD)
        clicked = _click_si_esta()
        intentos += 1
    if not clicked:
        ss(driver, f"ps_grupo_sin_resultado_{codigo[:10]}")
        raise ProductoNoEncontrado(
            f"Producto no encontrado en la lista del grupo (supplier={supplier!r} "
            f"codigo={codigo!r})")

    time.sleep(6 * VELOCIDAD)
    ok_ctx, menu_items = _en_contexto_producto(driver)
    if not ok_ctx:
        ss(driver, f"ps_grupo_contexto_incorrecto_{codigo[:10]}")
        raise ProductoNoEncontrado(
            f"Click en resultado OK pero no quedó en contexto de producto "
            f"(menú visto: {menu_items}) — codigo={codigo!r}")


def ordenar_cola_por_supplier(cola):
    """Agrupa los items del mismo SUPPLIER (aunque sean de distinta
    location) uno a continuación del otro, respetando el orden de primera
    aparición — así se encadenan con la lupa. Dentro de cada supplier se
    conserva el orden original."""
    orden, grupos = [], {}
    for it in cola:
        k = it["supplier"].strip().upper()
        if k not in grupos:
            grupos[k] = []
            orden.append(k)
        grupos[k].append(it)
    return [it for k in orden for it in grupos[k]]


def abrir_producto(driver, item, es_primero_de_grupo, grupo_multiple, estado_ok):
    """Abre `item` con el camino más corto disponible. `estado_ok` indica
    si el driver quedó parado en un producto abierto por el item anterior
    del mismo supplier (condición para usar la lupa)."""
    cod = item["codigo"]
    if es_primero_de_grupo and grupo_multiple:
        try:
            _abrir_primero_de_grupo(driver, item["supplier"], cod)
            return
        except ProductoNoEncontrado as e:
            print(f"    ⚠ {e} — probando búsqueda completa")
    elif estado_ok:
        # El detalle del período leído antes sigue abierto: se cierra con
        # EXIT y recién ahí se usa la lupa (con un diálogo abierto, la
        # lupa no responde o actúa sobre el diálogo equivocado).
        if cerrar_dialogos_abiertos(driver) and _saltar_a_producto_via_lupa(driver, cod):
            return
    buscar_producto(driver, item["location"], item["supplier"], cod,
                    service_type=item["service_type"])


def main():
    modo = MODO
    validar_config(modo)
    print(f"MODO={modo}  genérico={GENERICO or '-'}  locations={LOCATIONS or '(registradas)'}  "
          f"específicos={ESPECIFICOS or '-'}")
    if TIPO_CAMBIO_ARS_USD is None:
        print("⚠ Sin tipo de cambio ARS→USD — las filas en ARS quedan sin TARIFA_USD.")

    # Se abre el Sheet ANTES de loguear en Tourplan: un error de
    # credenciales/URL no debe gastar una de las licencias limitadas.
    sh = abrir_spreadsheet()
    ws_gen = obtener_hoja(sh, HOJA_GENERICOS, COLS_TARIFAS)
    ws_esp = obtener_hoja(sh, HOJA_ESPECIFICOS, COLS_TARIFAS)
    ws_cmp = obtener_hoja(sh, HOJA_COMPARACION, COLS_COMPARACION)

    esp_rows = leer_filas(ws_esp, normalizar_fila_tarifa)
    gen_rows = leer_filas(ws_gen, normalizar_fila_tarifa)
    cmp_rows = leer_filas(ws_cmp)
    if modo == "ACTUALIZAR" and not esp_rows and not ACTUALIZAR_SELECCION:
        print(f"No hay específicos registrados en la pestaña {HOJA_ESPECIFICOS} — "
              f"corré antes un modo ESPECIFICO o COMPLETO.")
        return

    plan = armar_plan(modo, esp_rows, gen_rows)
    if not plan:
        print("Nada para actualizar con esa selección.")
        return

    # ── Guardado: cada código se escribe en el Sheet apenas se lee; la
    # comparación se calcula UNA vez, al final, con todo ya exportado. ──
    ahora_locs = set(LOCATIONS)
    locs_sucias = set()      # locations con datos guardados desde la última comparación
    comparo_alguna_vez = False

    def limpiar_grupo(sup, loc, es_gen, vigentes):
        """Supplier leído completo y sin fallas: borra de lo archivado los
        códigos que ya no existen en Tourplan."""
        destino, ws = (gen_rows, ws_gen) if es_gen else (esp_rows, ws_esp)
        obsoletos = [i for i, f in enumerate(destino)
                     if f["SUPPLIER"] == sup and f["LOCATION"] == loc
                     and f["PRODUCT_CODE"] not in vigentes]
        if obsoletos:
            borrar_filas_hoja(sh, ws, obsoletos)
            for i in reversed(obsoletos):
                del destino[i]
            print(f"🧹 {ws.title}: {len(obsoletos)} filas de códigos que ya no existen en Tourplan")

    def actualizar_comparacion(locs):
        nonlocal cmp_rows, locs_sucias, comparo_alguna_vez
        locs = set(locs)
        if not locs:
            return
        comp = recalcular_comparacion(gen_rows, esp_rows, locs)
        ahora = datetime.now().isoformat(timespec="seconds")
        for f in comp:
            f["ACTUALIZADO"] = ahora
        cmp_rows = [f for f in cmp_rows if str(f.get("LOCATION", "")) not in locs] + comp
        reescribir_hoja(ws_cmp, COLS_COMPARACION, cmp_rows, formato_pct_col="DIFERENCIA_PCT")
        print(f"✅ {HOJA_COMPARACION}: {len(comp)} filas recalculadas ({', '.join(sorted(locs))})")
        locs_sucias -= locs
        comparo_alguna_vez = True

    driver = crear_driver()
    cola, abortado, fallo_fatal = [], False, False
    grupos_fallidos, procesados_ok = set(), 0
    try:
        login(driver)
        pedidos = set()
        for comps, ig, ie in plan:
            cola += descubrir_cola(driver, comps, ig, ie)
            for c in comps:
                if ig:
                    pedidos.add((c["generico"], c["location"]))
                if ie:
                    pedidos.update((sup, c["location"]) for sup in c.get("especificos", []))
        encontrados = {(i["supplier"], i["location"]) for i in cola}
        for sup, loc in sorted(pedidos - encontrados):
            print(f"⚠ Sin códigos para {sup!r} en {loc} — ¿código/nombre correcto y "
                  f"cargado en esa location? No se modifica nada de ese supplier.")
        cola = ordenar_cola_por_supplier(cola)
        if LIMIT_PRUEBA:
            print(f"\n⚠ LIMIT_PRUEBA={LIMIT_PRUEBA}: sólo los primeros {LIMIT_PRUEBA} "
                  f"de {len(cola)} códigos; no se borran códigos archivados.")
            cola = cola[:LIMIT_PRUEBA]
            grupos_fallidos.update((i["supplier"], i["location"]) for i in cola)
        print(f"Cola de trabajo: {len(cola)} códigos a leer.")

        vigentes_por_grupo, es_gen_grupo, restantes = {}, {}, {}
        for it in cola:
            g = (it["supplier"], it["location"])
            vigentes_por_grupo.setdefault(g, set()).add(it["codigo"])
            es_gen_grupo[g] = it["es_generico"]
            restantes[g] = restantes.get(g, 0) + 1
        cant_por_supplier, restantes_sup, locs_sup = {}, {}, {}
        for it in cola:
            k = it["supplier"].strip().upper()
            cant_por_supplier[k] = cant_por_supplier.get(k, 0) + 1
            restantes_sup[k] = restantes_sup.get(k, 0) + 1
            locs_sup.setdefault(k, set()).add(it["location"])

        supplier_anterior, pos_en_supplier, estado_ok = None, 0, False
        for i, item in enumerate(cola):
            chequear_abort()
            g = (item["supplier"], item["location"])
            k_sup = item["supplier"].strip().upper()
            if k_sup != supplier_anterior:
                supplier_anterior, pos_en_supplier, estado_ok = k_sup, 0, False
            print(f"\n[{i+1}/{len(cola)}] {item['location']}/{item['supplier']}/{item['codigo']}")
            try:
                abrir_producto(driver, item, pos_en_supplier == 0,
                               cant_por_supplier[k_sup] > 1, estado_ok)
                estado_ok = True
                tarifas = leer_tarifa_vigente_componente(driver, item["codigo"],
                                                         price_code=item["price_code"])
                cerrar_dialogos_abiertos(driver)
                if not tarifas:
                    print(f"    ⚠ Sin tarifas leídas para {item['codigo']}")
                filas_codigo = []
                for t in tarifas:
                    if t["moneda"] not in ("ARS", "USD"):
                        print(f"    ⚠ Moneda inesperada {t['moneda']!r} en {item['codigo']} "
                              f"(pax {t['pax_desde']}-{t['pax_hasta']}) — no se convierte a USD")
                    filas_codigo.append({
                        "SUPPLIER": item["supplier"], "PRODUCT_CODE": item["codigo"],
                        "ES_GENERICO": item["es_generico"], "GENERICO": item["generico"],
                        "DESCRIPCION": item.get("descripcion", ""),
                        "PERIODO_DESDE": t.get("periodo_desde", ""),
                        "PERIODO_HASTA": t.get("periodo_hasta", ""),
                        "PRICE_CODE": t.get("price_code", ""),
                        "PAX_DESDE": t["pax_desde"], "PAX_HASTA": t["pax_hasta"],
                        "TARIFA_VIGENTE": t["tarifa"], "MONEDA": t["moneda"],
                        "TARIFA_USD": convertir_a_usd(t["tarifa"], t["moneda"], TIPO_CAMBIO_ARS_USD),
                        "LOCATION": item["location"],
                        "TIMESTAMP": datetime.now().isoformat(timespec="seconds"),
                    })
                destino, ws_dest = (gen_rows, ws_gen) if item["es_generico"] else (esp_rows, ws_esp)
                reemplazar_filas_codigo(sh, ws_dest, COLS_TARIFAS, destino, g[0], g[1],
                                        item["codigo"], filas_codigo)
                print(f"    💾 {ws_dest.title}: {len(filas_codigo)} filas de {item['codigo']} guardadas")
                locs_sucias.add(item["location"])
                procesados_ok += 1
            except AbortadoPorUsuario:
                raise
            except ProductoNoEncontrado as e:
                print(f"    ❌ {e}")
                grupos_fallidos.add(g)
                estado_ok = False
            except Exception as e:
                print(f"    ❌ Error inesperado en {item['codigo']}: {e}")
                ss(driver, f"error_{item['codigo'][:10]}")
                grupos_fallidos.add(g)
                estado_ok = False
            pos_en_supplier += 1

            restantes[g] -= 1
            try:
                if restantes[g] == 0 and g not in grupos_fallidos:
                    limpiar_grupo(g[0], g[1], es_gen_grupo[g], vigentes_por_grupo[g])
                elif restantes[g] == 0:
                    print(f"⚠ {g[0]}/{g[1]}: lectura incompleta — sólo se pisaron los códigos leídos OK.")
            except Exception as e:
                print(f"    ⚠ No pude limpiar el Sheet de {g[0]}/{g[1]}: {e}")
            # Al terminar TODOS los códigos de un proveedor (en todas sus
            # locations) se compara; mientras se exporta el siguiente.
            restantes_sup[k_sup] -= 1
            if restantes_sup[k_sup] == 0:
                try:
                    actualizar_comparacion(locs_sup[k_sup])
                except Exception as e:
                    print(f"    ⚠ No pude actualizar la comparación (se reintenta al final): {e}")
    except AbortadoPorUsuario:
        abortado = True
        print("\n⏸️ Abortado por el usuario — se guarda lo ya leído.")
    except Exception:
        fallo_fatal = True
        print("\n❌ Error inesperado, se corta la corrida (se guarda lo ya leído):")
        traceback.print_exc()
    finally:
        try:
            logout(driver)
        except Exception as e:
            print(f"  ⚠ logout falló: {e}")
        driver.quit()

    # Cierre: comparar lo que quedó sin comparar (p. ej. si se abortó a
    # mitad de un proveedor); si no se comparó nada, todas las locations.
    locs_tocadas = {i["location"] for i in cola} | ahora_locs
    try:
        actualizar_comparacion(locs_sucias if comparo_alguna_vez else locs_tocadas)
    except Exception:
        print("❌ No pude guardar lo pendiente en el Sheet:")
        traceback.print_exc()
        sys.exit(1)

    comparacion = [f for f in cmp_rows if str(f.get("LOCATION", "")) in locs_tocadas]
    con_flags = [f for f in comparacion if f.get("FLAGS")]
    if con_flags:
        conteo = {}
        for f in con_flags:
            conteo[f["FLAGS"]] = conteo.get(f["FLAGS"], 0) + 1
        print(f"\n{len(con_flags)} filas con alguna bandera (revisar):")
        for flag, n in sorted(conteo.items(), key=lambda x: -x[1]):
            print(f"    {flag}: {n}")
    elif not comparacion:
        print("ℹ️ No hay específicos archivados en esas locations todavía — nada que comparar.")
    print(f"\nCódigos leídos OK en esta corrida: {procesados_ok}/{len(cola)}")

    if abortado:
        sys.exit(ABORT_EXIT_CODE)
    if fallo_fatal:
        sys.exit(1)


if __name__ == "__main__":
    main()
