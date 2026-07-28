# -*- coding: utf-8 -*-
"""
Fase 3: comparación de gap TRFPO (genérico) vs cada código específico
de transportista, a partir de la salida de Fase 1
(extraccion_tarifas_vigentes.py, tarifas_vigentes.xlsx).

No usa Description/vehículo/categoría en absoluto: a diferencia del
diseño original en BRIEF.md (pensado antes de tener rangos de pax
reales leídos de Tourplan), Fase 1 ya trae el PAX_DESDE/PAX_HASTA real
de cada código directamente de la grilla de RATES — no hace falta
volver a inferirlo por regex de texto ni cruzar contra
tabla_bases_vehiculo_pax.csv. El único cruce que hace falta es:

1. Código específico → código TRFPO: longest-prefix-match, reusando
   tal cual mejor_prefijo_trfpo de matching_engine.py (Fase 2).
2. Código TRFPO + ese match → fila de TRFPO cuyo rango de pax se
   superpone Y cuyo período se superpone (máxima superposición de
   fechas si hay más de uno) con la fila del transportista.
3. Gap = TARIFA_USD del transportista vs. TARIFA_USD de TRFPO.

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

from matching_engine import mejor_prefijo_trfpo

COLS_SALIDA = [
    "LOCATION", "CODIGO_TRFPO", "SUPPLIER_TRANSPORTISTA", "CODIGO_TRANSPORTISTA",
    "PAX_DESDE", "PAX_HASTA", "PERIODO_DESDE", "PERIODO_HASTA",
    "TARIFA_TRFPO_USD", "TARIFA_TRANSPORTISTA_USD",
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
    una fila por (rango de pax, período) de CADA código de
    transportista, comparado contra el TRFPO que le corresponde."""
    filas_salida = []

    for location, df_loc in df.groupby("LOCATION"):
        df_trfpo = df_loc[df_loc["ES_GENERICO"] == True]
        df_transp = df_loc[df_loc["ES_GENERICO"] == False]
        codigos_trfpo = sorted(df_trfpo["PRODUCT_CODE"].astype(str).unique().tolist(),
                                key=len, reverse=True)

        for _, fila_t in df_transp.iterrows():
            codigo_t = str(fila_t["PRODUCT_CODE"])
            base = {
                "LOCATION": location,
                "SUPPLIER_TRANSPORTISTA": fila_t["SUPPLIER"],
                "CODIGO_TRANSPORTISTA": codigo_t,
                "PAX_DESDE": fila_t["PAX_DESDE"],
                "PAX_HASTA": fila_t["PAX_HASTA"],
                "PERIODO_DESDE": fila_t["PERIODO_DESDE"],
                "PERIODO_HASTA": fila_t["PERIODO_HASTA"],
                "TARIFA_TRANSPORTISTA_USD": _r2(fila_t["TARIFA_USD"]),
            }

            mejor, candidatos = mejor_prefijo_trfpo(codigo_t, codigos_trfpo)
            flags = []
            if len(candidatos) > 1:
                flags.append("COLISION_REVISAR")
            if mejor is None:
                filas_salida.append({**base, "CODIGO_TRFPO": "",
                                      "TARIFA_TRFPO_USD": None,
                                      "DIFERENCIA_USD": None, "DIFERENCIA_PCT": None,
                                      "FLAGS": ",".join(flags + ["SIN_MATCH_TRFPO"])})
                continue

            candidatas_trfpo = df_trfpo[
                (df_trfpo["PRODUCT_CODE"].astype(str) == mejor) &
                (df_trfpo["PAX_DESDE"] <= fila_t["PAX_HASTA"]) &
                (df_trfpo["PAX_HASTA"] >= fila_t["PAX_DESDE"])
            ]
            if candidatas_trfpo.empty:
                filas_salida.append({**base, "CODIGO_TRFPO": mejor,
                                      "TARIFA_TRFPO_USD": None,
                                      "DIFERENCIA_USD": None, "DIFERENCIA_PCT": None,
                                      "FLAGS": ",".join(flags + ["SIN_TRFPO_PARA_ESE_PAX"])})
                continue

            fila_trfpo = max(
                candidatas_trfpo.to_dict("records"),
                key=lambda r: _dias_superposicion(
                    fila_t["_PERIODO_DESDE_DT"], fila_t["_PERIODO_HASTA_DT"],
                    r["_PERIODO_DESDE_DT"], r["_PERIODO_HASTA_DT"]))

            if _dias_superposicion(fila_t["_PERIODO_DESDE_DT"], fila_t["_PERIODO_HASTA_DT"],
                                    fila_trfpo["_PERIODO_DESDE_DT"], fila_trfpo["_PERIODO_HASTA_DT"]) == 0:
                flags.append("SIN_SUPERPOSICION_DE_PERIODO")

            tarifa_trfpo = _r2(fila_trfpo["TARIFA_USD"])
            tarifa_transp = _r2(fila_t["TARIFA_USD"])
            diff_usd = diff_pct = None
            if pd.notna(tarifa_trfpo) and pd.notna(tarifa_transp):
                diff_usd = round(tarifa_transp - tarifa_trfpo, 2)
                if tarifa_trfpo:
                    diff_pct = round((tarifa_transp / tarifa_trfpo - 1) * 100, 2)
            else:
                flags.append("SIN_TARIFA_USD_PARA_COMPARAR")

            filas_salida.append({
                **base, "CODIGO_TRFPO": mejor, "TARIFA_TRFPO_USD": tarifa_trfpo,
                "DIFERENCIA_USD": diff_usd, "DIFERENCIA_PCT": diff_pct,
                "FLAGS": ",".join(flags),
            })

    return pd.DataFrame(filas_salida, columns=COLS_SALIDA)


def main():
    entrada = sys.argv[1] if len(sys.argv) > 1 else "tarifas_vigentes.xlsx"
    salida = sys.argv[2] if len(sys.argv) > 2 else "comparacion_gap.xlsx"
    df = cargar_tarifas(entrada)
    comparacion = construir_comparacion(df)
    comparacion.to_excel(salida, index=False, sheet_name="COMPARACION_GAP")
    print(f"{len(comparacion)} filas escritas en {salida}")

    con_flags = comparacion[comparacion["FLAGS"] != ""]
    if not con_flags.empty:
        print(f"\n{len(con_flags)} filas con alguna bandera (revisar):")
        print(con_flags["FLAGS"].value_counts().to_string())


if __name__ == "__main__":
    main()
