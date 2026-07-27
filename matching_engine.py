# -*- coding: utf-8 -*-
"""
Motor de matching TRFPO (genérico) -> códigos específicos de transportista.

Fase 2 del proyecto (ver BRIEF.md). Puro pandas/regex, sin Selenium ni
dependencia de Tourplan — se puede correr y testear con los CSV de
Product List ya exportados (Loc,Serv,Supplier,SupplierName,Code,
Description,Comment,Used,Deleted) y la tabla_bases_vehiculo_pax.csv.

Algoritmo (ver BRIEF.md "Algoritmo de matching" para el detalle
completo, ya validado con datos reales por la usuaria):

1. Longest-prefix-match: el código TRFPO es siempre prefijo del código
   específico del transportista; ante colisión (más de un prefijo TRFPO
   matchea) se toma el más largo, y se deja igual una marca
   COLISION_REVISAR para que un humano confirme el caso — el ejemplo real
   HRD/HRD3/HRD4/HRD5 tiene un caso genuinamente ambiguo por texto solo
   (ver test_matching.py, caso HRD42): el algoritmo elige de forma
   determinística (el prefijo más largo), pero al haber colisión real
   queda marcado para revisión en vez de asumirse silenciosamente
   correcto.
2. Vehículo y rango de pax: se parsean del `Description` del código
   ESPECÍFICO (no de TRFPO, que no tiene vehículo en su texto) por
   regex — nunca del código.
3. Banderas: JAPON, CRUCERO, SIN_GUIA, CASO_ESPECIAL_SIB, NO_TRANSPORTE.
4. Categoría de servicio (EXCURSION/TRASLADO/JAPON/CRUCEROS_EXCURSION/
   CRUCEROS_TRASLADO) — para indexar contra la tabla de bases por
   (LOCATION, CATEGORIA, GUIA, PAX).
"""
import re
import unicodedata

import pandas as pd

# ── Parseo de rango de pax y vehículo desde Description ──────────────

RE_PAX_RANGO = re.compile(r'\((\d+)\s*/?\s*(\d*)\s*pax\)', re.IGNORECASE)

# Orden de prioridad (más específico primero) — ver BRIEF.md sección
# "Algoritmo de matching", punto 2. Los patrones de Doble Tracción y
# Hi Ace/Van 12 pax NO están confirmados contra datos reales todavía
# (no aparecen en las 3 muestras BUE disponibles) — ajustar el regex
# cuando lleguen CSV reales de FTE/USH con esos vehículos.
VEHICLE_PATTERNS = [
    (re.compile(r'\bAUTO\b', re.I), 'Auto'),
    (re.compile(r'\bH1\b|\bVITO\b', re.I), 'H1/VITO'),
    (re.compile(r'SPRINTER\s*15|\bSP\s*15\b', re.I), 'Sprinter 15 pax'),
    (re.compile(r'SPRINTER\s*19|\bSP\s*19\b|\bSP19\b', re.I), 'Sprinter 19 pax'),
    (re.compile(r'MINIBUS\s*24|\bMINIBUS\b', re.I), 'Minibus'),
    (re.compile(r'BUS\s*\+\s*(VAN|VH)', re.I), 'Bus + equipaje'),
    (re.compile(r'\bBUS\b', re.I), 'Bus'),
    # No confirmados con datos reales todavía (ver docstring del módulo):
    (re.compile(r'DOBLE\s*TRACCI[OÓ]N|\bDT\b|\b4X4\b', re.I), 'Doble Traccion (DT)'),
    (re.compile(r'HI\s*ACE|\bVAN\s*12\b', re.I), 'Hi Ace/Van 12 pax'),
]

# Patrones de clasificación EXCURSION (BRIEF.md sección 4). Todo lo que
# no matchea ninguno de acá es TRASLADO por default. "Hr(s) Dispo" usa
# regex en vez de substring plano porque las variantes con cantidad de
# horas ("3 Hrs Dispo", "4 Hrs Dispo", "5 Hrs Dispo" — HRD3/HRD4/HRD5)
# son plural y no matchean el substring literal "HR DISPO" — bug real
# encontrado corriendo test_matching.py contra los CSV reales: sin este
# regex, HRD319/HRD419/etc. quedaban en TRASLADO mientras que HRD
# (genérico, "Hr Dispo" singular) quedaba en EXCURSION, dos tramos de la
# misma familia de servicio con categoría distinta sin motivo real.
EXCURSION_PATTERNS = [
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

# NO_TRANSPORTE: códigos que no son servicios de transporte real
# (BRIEF.md sección 3). Detectados por texto de Description, no por
# código puntual (más robusto ante nuevos supplier codes).
NO_TRANSPORTE_PATTERNS = [
    re.compile(r'MINERAL\s*WATER', re.I),
    re.compile(r'^\s*TRANSFER\s*$', re.I),           # ej. "700TRF": "Transfer"
    re.compile(r'\bTR\s+GENERICO\b', re.I),          # ej. TRF19/24/42
]


def _norm(s):
    """minúsculas, sin acentos, sin espacios/puntuación — para comparar
    nombres de vehículo entre Description (regex) y tabla_bases (texto
    libre cargado a mano), que difieren en redacción exacta (ej. "Bus +
    VH para equipaje" vs "Bus + Van para equipaje")."""
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', s.lower())


def normalizar_vehiculo(nombre):
    """Colapsa variantes de texto de un mismo vehículo a una clave
    canónica, para poder matchear el vehículo parseado de Description
    contra el vehículo cargado en tabla_bases_vehiculo_pax.csv aunque la
    redacción no sea idéntica letra por letra."""
    n = _norm(nombre)
    if 'bus' in n and 'equipaj' in n:
        return 'BUS_EQUIPAJE'
    if n.startswith('bus') or n == 'bus':
        return 'BUS'
    if 'minibus' in n:
        return 'MINIBUS'
    if 'sprinter15' in n or 'sp15' in n:
        return 'SPRINTER15'
    if 'sprinter19' in n or 'sp19' in n:
        return 'SPRINTER19'
    if 'hiace' in n or 'van12' in n:
        return 'HIACE_VAN12'
    if 'h1' in n or 'vito' in n:
        return 'H1_VITO'
    if 'doble' in n and 'trac' in n:
        return 'DT'
    if 'auto' in n:
        return 'AUTO'
    return n.upper()


def parsear_pax_rango(description):
    """Devuelve (pax_desde, pax_hasta) o (None, None) si no matchea.
    Soporta '(5/11 pax)', '(1/2pax)', '(1 pax)'."""
    m = RE_PAX_RANGO.search(description or "")
    if not m:
        return None, None
    desde = int(m.group(1))
    hasta = int(m.group(2)) if m.group(2) else desde
    return desde, hasta


def parsear_vehiculo(description):
    """Devuelve el nombre de vehículo (tal como en VEHICLE_PATTERNS) o
    None si ninguna keyword conocida matchea — NUNCA se infiere de "la
    última palabra antes del paréntesis" (da falsos positivos con
    palabras como "Japon", "Crucero", "s/guia", "Euro" — ver BRIEF.md)."""
    desc = description or ""
    for patron, nombre in VEHICLE_PATTERNS:
        if patron.search(desc):
            return nombre
    return None


# ── Banderas ───────────────────────────────────────────────────────

def detectar_flags(codigo, description, comment=""):
    """Devuelve el set de banderas aplicables a una fila de Product
    List (BRIEF.md sección 3). No son mutuamente excluyentes."""
    codigo = str(codigo or "")
    desc = description or ""
    comment = comment or ""
    flags = set()

    if codigo.upper().startswith("J") or "japon" in desc.lower():
        flags.add("JAPON")

    if "crucero" in desc.lower():
        flags.add("CRUCERO")

    if "s/guia" in desc.lower() or codigo.upper().startswith("N"):
        flags.add("SIN_GUIA")

    if re.search(r'SI(B)?$', codigo.upper()) or "valoriza al 50%" in (desc + comment).lower():
        flags.add("CASO_ESPECIAL_SIB")

    if any(p.search(desc) for p in NO_TRANSPORTE_PATTERNS):
        flags.add("NO_TRANSPORTE")

    return flags


def clasificar_categoria(description, flags):
    """CATEGORIA para indexar contra tabla_bases_vehiculo_pax.csv.
    JAPON y CRUCERO son banderas que además determinan la categoría
    (BRIEF.md sección 4); SIN_GUIA no es una categoría — es la
    dimensión GUIA, tratada aparte (ver clasificar_guia)."""
    desc = description or ""
    es_excursion = any(p.search(desc) for p in EXCURSION_PATTERNS)

    if "JAPON" in flags:
        return "JAPON"
    if "CRUCERO" in flags:
        return "CRUCEROS_EXCURSION" if es_excursion else "CRUCEROS_TRASLADO"
    return "EXCURSION" if es_excursion else "TRASLADO"


def clasificar_guia(flags, categoria):
    """JAPON en tabla_bases_vehiculo_pax.csv no distingue CON/SIN_GUIA
    (una sola columna de pax) — para esa categoría se fuerza CON_GUIA
    como clave de lookup sin importar la bandera SIN_GUIA real (que
    igual se preserva en FLAGS de salida para auditoría)."""
    if categoria == "JAPON":
        return "CON_GUIA"
    return "SIN_GUIA" if "SIN_GUIA" in flags else "CON_GUIA"


# ── Longest-prefix-match código específico -> TRFPO ───────────────

def mejor_prefijo_trfpo(codigo_especifico, codigos_trfpo_por_largo_desc):
    """codigos_trfpo_por_largo_desc: lista de códigos TRFPO de la MISMA
    location, ordenada por longitud descendente. Devuelve
    (mejor_match, lista_de_todos_los_candidatos) — lista_de_candidatos
    con más de un elemento implica colisión (ver COLISION_REVISAR)."""
    candidatos = [c for c in codigos_trfpo_por_largo_desc if codigo_especifico.startswith(c)]
    if not candidatos:
        return None, []
    mejor = max(candidatos, key=len)
    return mejor, candidatos


# ── Carga de datos ─────────────────────────────────────────────────

COLS_PRODUCT_LIST = ["Loc", "Serv", "Supplier", "SupplierName", "Code",
                      "Description", "Comment", "Used", "Deleted"]


def cargar_product_list(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df[[c for c in COLS_PRODUCT_LIST if c in df.columns]].copy()
    for c in COLS_PRODUCT_LIST:
        if c not in df.columns:
            df[c] = ""
    df["Code"] = df["Code"].str.strip()
    # Excluir códigos borrados: no deberían participar del matching.
    df = df[df["Deleted"].str.upper() != "Y"].reset_index(drop=True)
    return df


def cargar_tabla_bases(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df["PAX_DESDE"] = df["PAX_DESDE"].astype(int)
    df["PAX_HASTA"] = df["PAX_HASTA"].astype(int)
    df["VEHICULO_NORM"] = df["VEHICULO"].apply(normalizar_vehiculo)
    return df


# ── Construcción de la tabla de mapeo ──────────────────────────────

COLS_MAPEO = ["CODIGO_TRFPO", "CATEGORIA", "GUIA", "CODIGO_REAL",
              "TRANSPORTISTA", "SUPPLIER", "LOCATION", "VEHICULO",
              "PAX_DESDE", "PAX_HASTA", "DESCRIPTION", "FLAGS"]


def construir_mapeo(df_trfpo, dfs_transportistas):
    """dfs_transportistas: lista de DataFrames (uno por transportista,
    ya cargados con cargar_product_list). Devuelve un DataFrame con
    una fila por código específico de transportista, con su match
    TRFPO (o SIN_MATCH), vehículo/pax parseados y flags — ver
    COLS_MAPEO. Índice implícito por (LOCATION, CATEGORIA, GUIA) para
    el join contra tabla_bases_vehiculo_pax.csv en Fase 3."""
    filas = []

    # TRFPO codes agrupados por LOCATION, ordenados por longitud desc
    # (más largo primero → longest-prefix-match directo).
    trfpo_por_loc = {}
    for loc, grupo in df_trfpo.groupby("Loc"):
        trfpo_por_loc[loc] = sorted(grupo["Code"].tolist(), key=len, reverse=True)

    for df_transp in dfs_transportistas:
        for _, fila in df_transp.iterrows():
            loc = fila["Loc"]
            codigo = fila["Code"]
            desc = fila["Description"]
            flags = detectar_flags(codigo, desc, fila.get("Comment", ""))

            codigos_trfpo = trfpo_por_loc.get(loc, [])
            mejor, candidatos = mejor_prefijo_trfpo(codigo, codigos_trfpo)
            if len(candidatos) > 1:
                flags.add("COLISION_REVISAR")
            if mejor is None:
                flags.add("SIN_MATCH")

            if "NO_TRANSPORTE" in flags:
                # Excluidos del matching por vehículo/pax (BRIEF.md
                # sección 3) — se listan aparte, sin vehículo/categoría.
                filas.append({
                    "CODIGO_TRFPO": mejor, "CATEGORIA": "", "GUIA": "",
                    "CODIGO_REAL": codigo, "TRANSPORTISTA": fila["SupplierName"],
                    "SUPPLIER": fila["Supplier"], "LOCATION": loc,
                    "VEHICULO": "", "PAX_DESDE": None, "PAX_HASTA": None,
                    "DESCRIPTION": desc, "FLAGS": ",".join(sorted(flags)),
                })
                continue

            categoria = clasificar_categoria(desc, flags)
            guia = clasificar_guia(flags, categoria)
            vehiculo = parsear_vehiculo(desc)
            pax_desde, pax_hasta = parsear_pax_rango(desc)

            if vehiculo is None:
                flags.add("SIN_VEHICULO_PARSEADO")
            if pax_desde is None:
                flags.add("SIN_PAX_PARSEADO")

            filas.append({
                "CODIGO_TRFPO": mejor, "CATEGORIA": categoria, "GUIA": guia,
                "CODIGO_REAL": codigo, "TRANSPORTISTA": fila["SupplierName"],
                "SUPPLIER": fila["Supplier"], "LOCATION": loc,
                "VEHICULO": vehiculo, "PAX_DESDE": pax_desde, "PAX_HASTA": pax_hasta,
                "DESCRIPTION": desc, "FLAGS": ",".join(sorted(flags)),
            })

    return pd.DataFrame(filas, columns=COLS_MAPEO)


def cubrir_vehiculos_esperados(df_tabla_bases, location, categoria, guia, pax_desde, pax_hasta):
    """Dado un tramo TRFPO (location/categoria/guia/rango de pax),
    devuelve la lista de vehículos válidos según tabla_bases_vehiculo_pax
    — puede ser más de uno (BRIEF.md: "múltiples vehículos por rango",
    ej. Auto y Doble Tracción cubriendo el mismo 1-2 pax en FTE/USH).
    Devuelve lista vacía + deja que el llamador marque
    SIN_TABLA_BASES_PARA_LOCATION si no hay ninguna fila para esa
    location en absoluto (distinto de "no hay vehículo para ese tramo
    puntual", que sí sería un tramo real sin cobertura)."""
    filas_loc = df_tabla_bases[df_tabla_bases["LOCATION"] == location]
    if filas_loc.empty:
        return None  # señal: no hay tabla de bases cargada para esta location

    filas = filas_loc[
        (filas_loc["CATEGORIA"] == categoria) &
        (filas_loc["GUIA"] == guia) &
        (filas_loc["PAX_DESDE"] <= pax_hasta) &
        (filas_loc["PAX_HASTA"] >= pax_desde)
    ]
    return filas["VEHICULO_NORM"].unique().tolist()
