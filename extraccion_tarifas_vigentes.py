# ============================================================
# TRFPO vs TARIFARIOS — FASE 1: extracción de tarifa vigente
# Google Colab — celda única (mismo formato que los scripts hermanos)
# ------------------------------------------------------------
#   Adaptado de:
#   - tourplan_valorizacion_pkg_v3.py (Tourplan-Valorizacion-EX-TF):
#     buscar_producto, _leer_tabla_rates, _extraer_valores_ad,
#     helpers base (ss/jc/set_val/wait/waitx/crear_driver/login/
#     hamburger/menu_item).
#   - cbd_impact_simulator_pkg.py (cbd-impact-simulator-pkg):
#     versión de buscar_producto que además abre el resultado y
#     confirma el contexto de producto (_en_contexto_producto),
#     STYPE_SIDEBAR.
#   Ver DISENO.md ("Reuso de las herramientas hermanas") para el
#   detalle de qué se tomó de cada una y qué se dejó afuera
#   (USED IN, PCM/Dashboard, subcode de moneda, recálculo, reversión —
#   nada de eso aplica: acá sólo se lee la tarifa vigente tal como está
#   cargada en el propio componente, sin simular nada).
#
#   ⚠ SIN VERIFICAR CONTRA TOURPLAN REAL TODAVÍA — ver DISENO.md.
#   Es esperable ajustar selectores en la primera corrida real, igual
#   que documentan los scripts hermanos en su propio historial.
# ============================================================

import os, sys, subprocess, importlib, time, re, csv
from datetime import datetime

print("🔧 Verificando entorno...\n")

_PIPS_NEEDED = {
    "selenium":          "selenium",
    "webdriver_manager": "webdriver-manager",
}
_pips_faltantes = [pkg for mod, pkg in _PIPS_NEEDED.items()
                   if importlib.util.find_spec(mod) is None]
if _pips_faltantes:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q"] + _pips_faltantes, check=True)

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

# ── Config ────────────────────────────────────────────────────
USERNAME   = "poner minusculas"
PASSWORD   = "password"
BASE_URL   = "https://tourplannx.eurotur.com.ar/TourplanNX_Test"

# Cola de entrada: uno o más Product List export de TP (Loc,Serv,
# Supplier,SupplierName,Code,Description,Comment,Used,Deleted) — el
# mismo formato que ya usa el motor de matching (matching_engine.py).
# Se puede pasar el de TRFPO + el de cada transportista a comparar.
PRODUCT_LIST_CSVS = [
    "muestras/Product_List_TRFPO.csv",
    "muestras/Product_List_TurismoElPuente_1TEP01.csv",
    "muestras/Product_List_HousemanCars_6HOUS1.csv",
]
OUTPUT_CSV = "tarifas_vigentes.csv"
SS_DIR     = "screenshots"
os.makedirs(SS_DIR, exist_ok=True)

# Tipo de cambio ARS→USD a aplicar sobre las filas cuya moneda leída en
# RATES (columna BUY/SELL CURRENCY) sea ARS — dato manual, cargado por
# la usuaria antes de correr (ver config/tipo_cambio.csv, DISENO.md
# sección "Moneda"). La moneda en sí NO se configura a mano: se lee por
# línea directamente de Tourplan.
TIPO_CAMBIO_CSV = "config/tipo_cambio.csv"

VELOCIDAD = 1.0  # multiplicador de todos los time.sleep — subir si la red es lenta

_ss_n = [0]


# ── Helpers base (REUTILIZADO tal cual de los scripts hermanos) ────

def ss(driver, nombre):
    _ss_n[0] += 1
    p = f"{SS_DIR}/{_ss_n[0]:03d}_{nombre[:40]}_{int(time.time())}.png"
    driver.save_screenshot(p)
    print(f"  📸 {os.path.basename(p)}")


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
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1366,911")
    opts.add_argument("--disable-gpu")
    drv_path = ChromeDriverManager().install()
    svc = Service(executable_path=drv_path)
    d = webdriver.Chrome(service=svc, options=opts)
    print("✅ Driver iniciado")
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


def buscar_producto(driver, location, supplier, codigo, service_type=None):
    """Busca Location/Supplier/Code(/ServiceType) en Product Search,
    abre el resultado y confirma que quedó en contexto de producto
    (menú con RATES/UTILITIES/etc.). Adaptado tal cual de
    cbd_impact_simulator_pkg.py::buscar_producto — ver DISENO.md."""
    codigo = str(codigo).strip() if codigo not in (None, "") else ""
    location = str(location).strip() if location not in (None, "") else ""
    supplier = str(supplier).strip() if supplier not in (None, "") else ""
    st_upper = (service_type or "").strip().upper()
    print(f"\n  📦 Buscando: {location}/{supplier}/{codigo}"
          f"{('/' + st_upper) if st_upper else ''}")

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


# ── Lectura de la grilla RATES del componente (REUTILIZADO de
# tourplan_valorizacion_pkg_v3.py — _leer_tabla_rates/_extraer_valores_ad,
# generalizado para devolver el rango de pax desde/hasta en vez de sólo
# la etiqueta de fila) ──────────────────────────────────────────────

def _leer_tabla_rates(driver):
    return driver.execute_script("""
        for(var t of document.querySelectorAll('table')){
            var ths = Array.from(t.querySelectorAll('th'));
            if(ths.some(h => h.innerText.includes('GROUP COST') ||
                              h.innerText.includes('COST'))){
                return {
                    headers: ths.map(h => h.innerText.trim()),
                    rows: Array.from(t.querySelectorAll('tbody tr')).map(tr => ({
                        celdas: Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim()),
                        inputs: Array.from(tr.querySelectorAll('input')).map(i => i.value.trim())
                    }))
                };
            }
        }
        return null;
    """)


def leer_tarifa_vigente_componente(driver, codigo):
    """Abre el tab RATES del producto ya en contexto (ver
    buscar_producto) y devuelve una lista de {pax_desde, pax_hasta,
    tarifa, moneda} — sólo filas de rango de pax adulto (AD), igual
    criterio que _extraer_valores_ad. NO recorre históricos de período —
    lee la grilla tal como aparece por defecto (se asume que Tourplan
    muestra ahí la tarifa vigente; sin confirmar contra una corrida real
    con más de un período cargado — ver DISENO.md).

    Moneda: cada período de Rates tiene columnas BUY CURRENCY y SELL
    CURRENCY, por defecto cargadas iguales — confirmado por la usuaria,
    así que alcanza con leer una (se prioriza BUY si ambas existen). No
    hay fallback a config manual: si esta columna no aparece en la
    grilla real, la fila queda con moneda vacía y se avisa (ver
    DISENO.md), en vez de asumir una moneda por transportista."""
    hamburger(driver)
    menu_item(driver, "RATES")
    time.sleep(3 * VELOCIDAD)
    ss(driver, f"rates_{codigo[:10]}")

    tabla = _leer_tabla_rates(driver)
    if not tabla:
        dump(driver, f"rates_sin_tabla_{codigo[:10]}")
        print(f"    ⚠ No encontré tabla de RATES para {codigo}")
        return []

    headers = tabla["headers"]
    idx_svc = next((i for i, h in enumerate(headers) if "SERVICE" in h.upper()), 0)
    idx_cost = next((i for i, h in enumerate(headers)
                      if "GROUP COST" in h.upper() and "FIT" not in h.upper()), 1)
    idx_ccy = next((i for i, h in enumerate(headers)
                     if "BUY" in h.upper() and "CURRENC" in h.upper()), None)
    if idx_ccy is None:
        idx_ccy = next((i for i, h in enumerate(headers) if "CURRENC" in h.upper()), None)
    pat_rango = re.compile(r'(\d+)\s*[-–]\s*(\d+)')

    if idx_ccy is None:
        print(f"    ⚠ No encontré columna de moneda (BUY/SELL CURRENCY) en RATES "
              f"para {codigo} — headers: {headers}")

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
        raw = row["inputs"][0] if (row["inputs"] and row["inputs"][0]) else (
            celdas[idx_cost] if idx_cost < len(celdas) else "")
        try:
            valor = float(str(raw).replace(",", ".").replace("$", "").strip())
        except Exception:
            valor = None
        moneda = celdas[idx_ccy].strip().upper() if (idx_ccy is not None and idx_ccy < len(celdas)) else ""
        out.append({
            "pax_desde": int(m.group(1)),
            "pax_hasta": int(m.group(2)),
            "tarifa": valor,
            "moneda": moneda,
        })
    return out


# ── Cola de trabajo y moneda ─────────────────────────────────────────

def leer_cola_desde_product_lists(paths):
    """Junta los Product List CSV (Loc,Serv,Supplier,SupplierName,Code,
    Description,Comment,Used,Deleted) en la cola de trabajo — un
    (LOCATION, SUPPLIER, CODE, SERVICE_TYPE) por fila, saltando
    Deleted=Y. SERVICE_TYPE se toma de la columna Serv del propio CSV
    (ej. "TR")."""
    cola = []
    for path in paths:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for fila in csv.DictReader(f):
                if (fila.get("Deleted") or "").strip().upper() == "Y":
                    continue
                cola.append({
                    "location": (fila.get("Loc") or "").strip(),
                    "supplier": (fila.get("Supplier") or "").strip(),
                    "codigo": (fila.get("Code") or "").strip(),
                    "service_type": (fila.get("Serv") or "").strip(),
                })
    return cola


def cargar_tipo_cambio(path):
    """Tipo de cambio ARS→USD dado a mano (ver config/tipo_cambio.csv,
    DISENO.md sección Moneda). Un único valor — no varía por
    transportista, a diferencia de la moneda (que sí se lee por línea
    directamente de Tourplan, ver leer_tarifa_vigente_componente)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        fila = next(csv.DictReader(f), None)
    valor = (fila or {}).get("TIPO_CAMBIO_ARS_USD", "").strip()
    if not valor:
        return None
    return float(valor.replace(",", "."))


def convertir_a_usd(tarifa, moneda, tipo_cambio):
    """None si no se puede convertir con confianza: falta la tarifa,
    la moneda vino vacía (columna CURRENCY no encontrada), la moneda no
    es ARS ni USD, o es ARS pero no hay tipo de cambio cargado — mejor
    dejar el dato en blanco para revisión manual que inventar un
    valor."""
    if tarifa is None or not moneda:
        return None
    if moneda == "USD":
        return tarifa
    if moneda == "ARS":
        return tarifa / tipo_cambio if tipo_cambio else None
    return None


# ── Main ──────────────────────────────────────────────────────────

def main():
    cola = leer_cola_desde_product_lists(PRODUCT_LIST_CSVS)
    tipo_cambio = cargar_tipo_cambio(TIPO_CAMBIO_CSV)
    if tipo_cambio is None:
        print(f"⚠ Sin TIPO_CAMBIO_ARS_USD cargado en {TIPO_CAMBIO_CSV} — "
              f"las filas en ARS quedarán sin TARIFA_USD hasta completarlo.")
    print(f"Cola de trabajo: {len(cola)} códigos a leer.")

    driver = crear_driver()
    filas_salida = []
    try:
        login(driver)
        for i, item in enumerate(cola):
            print(f"\n[{i+1}/{len(cola)}] {item['supplier']}/{item['codigo']}")
            try:
                buscar_producto(driver, item["location"], item["supplier"],
                                 item["codigo"], service_type=item["service_type"])
                tarifas = leer_tarifa_vigente_componente(driver, item["codigo"])
                if not tarifas:
                    print(f"    ⚠ Sin tarifas leídas para {item['codigo']}")
                for t in tarifas:
                    if t["moneda"] not in ("ARS", "USD"):
                        print(f"    ⚠ Moneda inesperada {t['moneda']!r} en "
                              f"{item['codigo']} (pax {t['pax_desde']}-{t['pax_hasta']})"
                              f" — no se convierte a USD")
                    filas_salida.append({
                        "SUPPLIER": item["supplier"],
                        "PRODUCT_CODE": item["codigo"],
                        "PAX_DESDE": t["pax_desde"],
                        "PAX_HASTA": t["pax_hasta"],
                        "TARIFA_VIGENTE": t["tarifa"],
                        "MONEDA": t["moneda"],
                        "TARIFA_USD": convertir_a_usd(t["tarifa"], t["moneda"], tipo_cambio),
                        "LOCATION": item["location"],
                        "TIMESTAMP": datetime.now().isoformat(timespec="seconds"),
                    })
            except ProductoNoEncontrado as e:
                print(f"    ❌ {e}")
            except Exception as e:
                print(f"    ❌ Error inesperado en {item['codigo']}: {e}")
                ss(driver, f"error_{item['codigo'][:10]}")
    finally:
        driver.quit()

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        cols = ["SUPPLIER", "PRODUCT_CODE", "PAX_DESDE", "PAX_HASTA",
                "TARIFA_VIGENTE", "MONEDA", "TARIFA_USD", "LOCATION", "TIMESTAMP"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(filas_salida)
    print(f"\n✅ {len(filas_salida)} filas escritas en {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
