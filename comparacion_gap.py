# -*- coding: utf-8 -*-
"""
Fase 3: comparación de gap genérico (TRFPO, PEAPO, GUIAPO, o cualquier
otro que se agregue en el futuro) vs cada código específico, a partir
de la salida de Fase 1 (extraccion_tarifas_vigentes.py,
tarifas_vigentes.xlsx).

No usa Description/vehículo/categoría en absoluto: a diferencia del
diseño original en BRIEF.md (pensado antes de tener rangos de pax
reales leídos de Tourplan), Fase 1 ya trae el PAX_DESDE/PAX_HASTA real
de cada código directamente de la grilla de RATES — no hace falta
volver a inferirlo por regex de texto ni cruzar contra
tabla_bases_vehiculo_pax.csv. El único cruce que hace falta es:

1. Código específico → código genérico: longest-prefix-match, reusando
   tal cual mejor_prefijo_trfpo de matching_engine.py (Fase 2) — el
   algoritmo es puro string matching y nunca dependió de que el
   genérico fuera TRFPO en particular.
2. Código genérico + ese match → fila del genérico cuyo rango de pax
   se superpone Y cuyo período se superpone (máxima superposición de
   fechas si hay más de uno) con la fila del específico. Si
   `tarifas_vigentes.xlsx` trae la columna `GENERICO` (Fase 1 la
   agrega desde que se generalizó a PEAPO/GUIAPO), el cruce además se
   restringe al genérico de esa relación puntual — así, si dos
   genéricos comparten LOCATION (ej. TRFPO y PEAPO ambos en BUE), no
   se mezclan sus filas. Si la columna no está (Excel viejo, de antes
   de la generalización), se cae al comportamiento anterior de agrupar
   sólo por LOCATION.
3. Gap = TARIFA_USD del específico vs. TARIFA_USD del genérico.

NOTA: `extraccion_tarifas_vigentes.py` (Fase 1) ya corre esta misma
comparación automáticamente al final de su propia ejecución (versión
en Python puro, sin pandas, embebida ahí para que una sola corrida de
Colab genere los dos Excel de una — ver DISENO.md). Este archivo queda
para volver a correr SÓLO la comparación sin re-scrapear Tourplan (ej.
después de editar `tarifas_vigentes.xlsx` a mano, o para reprocesar con
otro archivo de entrada).

Requiere pandas + openpyxl. Uso:
    python comparacion_gap.py [tarifas_vigentes.xlsx] [salida.xlsx]
(default: tarifas_vigentes.xlsx → comparacion_gap.xlsx)
"""
import sys
from datetime import datetime

import pandas as pd
from openpyxl import load_workbook

# El motor de matching (Fase 2) sigue llamándose mejor_prefijo_trfpo
# porque matching_engine.py es intencionalmente específico del dominio
# de transporte (ver DISENO.md) y no se generalizó — pero el algoritmo
# en sí es puro string-prefix-match, así que se reusa tal cual para
# cualquier relación genérico/específico.
from matching_engine import mejor_prefijo_trfpo as mejor_prefijo_generico

COLS_GAP = [
    "LOCATION", "CODIGO_GENERICO", "SUPPLIER_ESPECIFICO", "CODIGO_ESPECIFICO",
    "PAX_DESDE", "PAX_HASTA",
    "PERIODO_ESPECIFICO_DESDE", "PERIODO_ESPECIFICO_HASTA",
    "PERIODO_GENERICO_DESDE", "PERIODO_GENERICO_HASTA",
    "TARIFA_GENERICO_USD", "TARIFA_ESPECIFICO_USD",
    "DIFERENCIA_USD", "DIFERENCIA_PCT", "FLAGS",
]


def _parsear_fecha(txt):
    if not txt or (isinstance(txt, float) and pd.isna(txt)):
        return None
    if isinstance(txt, datetime):
        return txt
    try:
        return datetime.strptime(str(txt).strip(), "%d/%m/%Y")
    except ValueError:
        return None


def _r2(x):
    """Redondeo defensivo a 2 decimales — el Excel de entrada puede
    traer más decimales si viene de una división (ej. conversión ARS
    con un tipo de cambio no redondo)."""
    return round(x, 2) if pd.notna(x) else None


def _dias_superposicion(desde1, hasta1, desde2, hasta2):
    """Días de superposición entre dos rangos de fechas. 0 (no
    negativo) si no se superponen o si falta alguna fecha — para poder
    comparar candidatos con max() sin excepciones por None."""
    if not (desde1 and hasta1 and desde2 and hasta2):
        return 0
    inicio = max(desde1, desde2)
    fin = min(hasta1, hasta2)
    return max(0, (fin - inicio).days)


def cargar_tarifas(path):
    df = pd.read_excel(path)
    # Descarta el catch-all de pax abierto hacia arriba (ej. "42-9999
    # AD", siempre 0) — defensivo, por si el Excel de entrada viene de
    # una corrida vieja de extraccion_tarifas_vigentes.py que todavía
    # no filtraba esto en el origen.
    df = df[df["PAX_HASTA"] != 9999].reset_index(drop=True)
    df["_PERIODO_DESDE_DT"] = df["PERIODO_DESDE"].apply(_parsear_fecha)
    df["_PERIODO_HASTA_DT"] = df["PERIODO_HASTA"].apply(_parsear_fecha)
    return df


def construir_comparacion(df):
    """df: DataFrame con las columnas de tarifas_vigentes.xlsx (una
    fila por código+período+rango de pax). Devuelve un DataFrame con
    una fila por (rango de pax, período) de CADA código específico,
    comparado contra el genérico que le corresponde."""
    filas_salida = []
    tiene_col_generico = "GENERICO" in df.columns

    for location, df_loc in df.groupby("LOCATION"):
        df_generico_loc = df_loc[df_loc["ES_GENERICO"] == True]
        df_especifico_loc = df_loc[df_loc["ES_GENERICO"] == False]

        for _, fila_e in df_especifico_loc.iterrows():
            codigo_e = str(fila_e["PRODUCT_CODE"])

            # Si el Excel trae GENERICO, se restringe el pool de
            # candidatos genéricos al de esta relación puntual — evita
            # mezclar TRFPO/PEAPO/GUIAPO cuando comparten LOCATION.
            # Si no lo trae (Excel de antes de la generalización), se
            # cae al comportamiento anterior de agrupar sólo por
            # LOCATION.
            if tiene_col_generico and pd.notna(fila_e.get("GENERICO")):
                df_generico = df_generico_loc[df_generico_loc["GENERICO"] == fila_e["GENERICO"]]
                if df_generico.empty:
                    df_generico = df_generico_loc
            else:
                df_generico = df_generico_loc

            codigos_generico = sorted(df_generico["PRODUCT_CODE"].astype(str).unique().tolist(),
                                       key=len, reverse=True)

            base = {
                "LOCATION": location,
                "SUPPLIER_ESPECIFICO": fila_e["SUPPLIER"],
                "CODIGO_ESPECIFICO": codigo_e,
                "PAX_DESDE": fila_e["PAX_DESDE"],
                "PAX_HASTA": fila_e["PAX_HASTA"],
                "PERIODO_ESPECIFICO_DESDE": fila_e["PERIODO_DESDE"],
                "PERIODO_ESPECIFICO_HASTA": fila_e["PERIODO_HASTA"],
                "PERIODO_GENERICO_DESDE": "", "PERIODO_GENERICO_HASTA": "",
                "TARIFA_ESPECIFICO_USD": _r2(fila_e["TARIFA_USD"]),
            }

            mejor, candidatos = mejor_prefijo_generico(codigo_e, codigos_generico)
            flags = []
            if len(candidatos) > 1:
                flags.append("COLISION_REVISAR")
            if mejor is None:
                filas_salida.append({**base, "CODIGO_GENERICO": "",
                                      "TARIFA_GENERICO_USD": None,
                                      "DIFERENCIA_USD": None, "DIFERENCIA_PCT": None,
                                      "FLAGS": ",".join(flags + ["SIN_MATCH_GENERICO"])})
                continue

            candidatas_generico = df_generico[
                (df_generico["PRODUCT_CODE"].astype(str) == mejor) &
                (df_generico["PAX_DESDE"] <= fila_e["PAX_HASTA"]) &
                (df_generico["PAX_HASTA"] >= fila_e["PAX_DESDE"])
            ]
            if candidatas_generico.empty:
                filas_salida.append({**base, "CODIGO_GENERICO": mejor,
                                      "TARIFA_GENERICO_USD": None,
                                      "DIFERENCIA_USD": None, "DIFERENCIA_PCT": None,
                                      "FLAGS": ",".join(flags + ["SIN_GENERICO_PARA_ESE_PAX"])})
                continue

            # Más de un período del genérico cargado para este mismo
            # rango de pax (ej. cambió su tarifa a mitad del período
            # del específico): se usa el de MAYOR superposición de
            # fechas, marcado con MULTIPLES_PERIODOS_GENERICO para no
            # descartar el resto en silencio.
            if len(candidatas_generico) > 1:
                flags.append("MULTIPLES_PERIODOS_GENERICO")

            fila_generico = max(
                candidatas_generico.to_dict("records"),
                key=lambda r: _dias_superposicion(
                    fila_e["_PERIODO_DESDE_DT"], fila_e["_PERIODO_HASTA_DT"],
                    r["_PERIODO_DESDE_DT"], r["_PERIODO_HASTA_DT"]))

            if _dias_superposicion(fila_e["_PERIODO_DESDE_DT"], fila_e["_PERIODO_HASTA_DT"],
                                    fila_generico["_PERIODO_DESDE_DT"], fila_generico["_PERIODO_HASTA_DT"]) == 0:
                flags.append("SIN_SUPERPOSICION_DE_PERIODO")

            tarifa_generico = _r2(fila_generico["TARIFA_USD"])
            tarifa_especifico = _r2(fila_e["TARIFA_USD"])
            diff_usd = diff_pct = None
            if pd.notna(tarifa_generico) and pd.notna(tarifa_especifico):
                diff_usd = round(tarifa_especifico - tarifa_generico, 2)
                if tarifa_generico:
                    diff_pct = round((tarifa_especifico / tarifa_generico - 1) * 100, 2)
            else:
                flags.append("SIN_TARIFA_USD_PARA_COMPARAR")

            filas_salida.append({
                **base, "CODIGO_GENERICO": mejor,
                "PERIODO_GENERICO_DESDE": fila_generico["PERIODO_DESDE"],
                "PERIODO_GENERICO_HASTA": fila_generico["PERIODO_HASTA"],
                "TARIFA_GENERICO_USD": tarifa_generico,
                "DIFERENCIA_USD": diff_usd, "DIFERENCIA_PCT": diff_pct,
                "FLAGS": ",".join(flags),
            })

    return pd.DataFrame(filas_salida, columns=COLS_GAP)


def _formatear_columna_porcentaje(path_xlsx, nombre_columna):
    """Aplica formato de número con signo % (visual, sin multiplicar
    por 100 — DIFERENCIA_PCT ya está en escala porcentual, ej. -44.76
    significa -44.76%, no -0.4476) a una columna del Excel ya guardado.
    pandas.to_excel no permite formato por celda directamente, así que
    se reabre con openpyxl para aplicarlo y se vuelve a guardar."""
    wb = load_workbook(path_xlsx)
    hoja = wb.active
    headers = [c.value for c in hoja[1]]
    if nombre_columna not in headers:
        return
    idx = headers.index(nombre_columna) + 1
    for fila in hoja.iter_rows(min_row=2, min_col=idx, max_col=idx):
        for celda in fila:
            if isinstance(celda.value, (int, float)):
                celda.number_format = '0.00"%"'
    wb.save(path_xlsx)


def main():
    entrada = sys.argv[1] if len(sys.argv) > 1 else "tarifas_vigentes.xlsx"
    salida = sys.argv[2] if len(sys.argv) > 2 else "comparacion_gap.xlsx"
    df = cargar_tarifas(entrada)
    comparacion = construir_comparacion(df)
    comparacion.to_excel(salida, index=False, sheet_name="COMPARACION_GAP")
    _formatear_columna_porcentaje(salida, "DIFERENCIA_PCT")
    print(f"{len(comparacion)} filas escritas en {salida}")

    con_flags = comparacion[comparacion["FLAGS"] != ""]
    if not con_flags.empty:
        print(f"\n{len(con_flags)} filas con alguna bandera (revisar):")
        print(con_flags["FLAGS"].value_counts().to_string())


if __name__ == "__main__":
    main()
