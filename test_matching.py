# -*- coding: utf-8 -*-
"""
Test de validación del motor de matching contra los 3 Product List
reales adjuntados por la usuaria (TRFPO + Turismo El Puente + Houseman
Cars, todos BUE) y la tabla de bases vehículo/pax.

No es un test unitario formal (no hay valores "esperados" cargados a
mano por transportista completo) — es una corrida de validación que
imprime los casos que el BRIEF.md marca explícitamente como
interesantes (colisiones, Japón sin la palabra en el texto, NO_TRANSPORTE)
para confirmar visualmente que el motor los resuelve como se espera, más
un resumen de flags para detectar sorpresas.

Correr: python test_matching.py
"""
import pandas as pd

from matching_engine import (
    cargar_product_list, cargar_tabla_bases, construir_mapeo,
    mejor_prefijo_trfpo,
)

MUESTRAS = "muestras"
CONFIG = "config"


def main():
    df_trfpo = cargar_product_list(f"{MUESTRAS}/Product_List_TRFPO.csv")
    df_tep = cargar_product_list(f"{MUESTRAS}/Product_List_TurismoElPuente_1TEP01.csv")
    df_houseman = cargar_product_list(f"{MUESTRAS}/Product_List_HousemanCars_6HOUS1.csv")
    df_tabla_bases = cargar_tabla_bases(f"{CONFIG}/tabla_bases_vehiculo_pax.csv")

    mapeo = construir_mapeo(df_trfpo, [df_tep, df_houseman])
    mapeo.to_csv("muestras/mapeo_resultado.csv", index=False)
    print(f"Total filas mapeadas: {len(mapeo)}\n")

    print("=" * 70)
    print("CASOS PUNTUALES CITADOS EN EL BRIEF")
    print("=" * 70)

    casos = [
        ("EZHT19", "Sprinter 19, debe matchear EZHT (TRFPO)"),
        ("EZHT24", "Minibus 24, debe matchear EZHT"),
        ("EZHT42", "Bus, debe matchear EZHT"),
        ("EZHTAU", "Auto (Houseman), debe matchear EZHT"),
        ("FARAU", "debe matchear FAR (no FARC)"),
        ("FARCAU", "debe matchear FARC (colisión FAR/FARC)"),
        ("JFAR", "no existe en muestras — sería Japón por PREFIJO J sin la palabra 'Japon' en texto"),
        ("JAEE19", "Japón por prefijo J, descripción SÍ dice 'Japon' → doble señal"),
        ("HRD19", "genérico, debe matchear HRD"),
        ("HRD319", "x3, debe matchear HRD3 (colisión HRD/HRD3)"),
        ("HRD419", "x4, debe matchear HRD4 (colisión HRD/HRD4)"),
        ("HRD42", "AMBIGUO: generico+Bus, pero 'HRD42' también empieza con 'HRD4' → "
                   "ver si el motor lo marca COLISION_REVISAR (esperado)"),
        ("HRD442", "x4+Bus, sin ambigüedad real (HRD44 no existe como TRFPO)"),
        ("700TRF", "NO_TRANSPORTE esperado"),
        ("MINWAT", "NO_TRANSPORTE esperado"),
        ("TRF19", "no está en Houseman, sí en Turismo El Puente — 'TR Generico' → NO_TRANSPORTE esperado"),
    ]
    for codigo, nota in casos:
        fila = mapeo[mapeo["CODIGO_REAL"] == codigo]
        if fila.empty:
            print(f"  {codigo:10s} (no está en las muestras cargadas) — {nota}")
            continue
        for _, f in fila.iterrows():
            trfpo = f['CODIGO_TRFPO'] if pd.notna(f['CODIGO_TRFPO']) and f['CODIGO_TRFPO'] else '(SIN_MATCH)'
            print(f"  {codigo:10s} -> TRFPO={trfpo:8s} "
                  f"CAT={f['CATEGORIA']:20s} VEH={str(f['VEHICULO']):15s} "
                  f"PAX=({f['PAX_DESDE']},{f['PAX_HASTA']}) FLAGS={f['FLAGS']}")
        print(f"             nota: {nota}")

    print("\n" + "=" * 70)
    print("RESUMEN DE FLAGS (cuántas filas tienen cada flag)")
    print("=" * 70)
    todas_flags = mapeo["FLAGS"].str.split(",").explode()
    print(todas_flags.value_counts().to_string())

    print("\n" + "=" * 70)
    print("SIN_MATCH (códigos que no matchearon ningún TRFPO) — revisar a mano")
    print("=" * 70)
    sin_match = mapeo[mapeo["CODIGO_TRFPO"].isna()]
    print(sin_match[["CODIGO_REAL", "TRANSPORTISTA", "DESCRIPTION"]].to_string(index=False))

    print("\n" + "=" * 70)
    print("SIN_VEHICULO_PARSEADO (no matcheó ninguna keyword de vehículo)")
    print("=" * 70)
    sin_veh = mapeo[mapeo["FLAGS"].str.contains("SIN_VEHICULO_PARSEADO", na=False)]
    print(sin_veh[["CODIGO_REAL", "DESCRIPTION", "FLAGS"]].to_string(index=False))


if __name__ == "__main__":
    main()
