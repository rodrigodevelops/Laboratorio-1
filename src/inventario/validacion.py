"""Validación de los archivos que llegan del sistema de origen.

Separa dos tipos de problemas:
- Errores (``DatosInvalidos``): el archivo no se usa. Esquema roto, valores
  imposibles, duplicados, tiendas o familias desconocidas.
- Advertencias: situaciones esperadas del negocio que no detienen el flujo,
  como precios de petróleo vacíos.
"""

from __future__ import annotations

import pandas as pd

LLAVES = {
    "ventas": ["date", "store_nbr", "family"],
    "promociones": ["date", "store_nbr", "family"],
    "transacciones": ["date", "store_nbr"],
    "petroleo": ["date"],
    "feriados": None,
}

COLUMNAS = {
    "ventas": ["date", "store_nbr", "family", "sales"],
    "promociones": ["date", "store_nbr", "family", "onpromotion"],
    "transacciones": ["date", "store_nbr", "transactions"],
    "petroleo": ["date", "dcoilwtico"],
    "feriados": ["date", "type", "locale", "locale_name", "description", "transferred"],
}

# Columnas numéricas que no pueden ser nulas ni negativas.
NO_NEGATIVAS = {"ventas": "sales", "promociones": "onpromotion", "transacciones": "transactions"}


class DatosInvalidos(Exception):
    def __init__(self, errores: list[str]):
        super().__init__("; ".join(errores))
        self.errores = errores


def validar(
    df: pd.DataFrame,
    fuente: str,
    tiendas: set[int] | None = None,
    familias: set[str] | None = None,
    fecha_esperada: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Devuelve el DataFrame con tipos correctos y la lista de advertencias.

    Lanza ``DatosInvalidos`` con todos los errores encontrados.
    """
    errores: list[str] = []
    advertencias: list[str] = []

    faltantes = [c for c in COLUMNAS[fuente] if c not in df.columns]
    if faltantes:
        raise DatosInvalidos([f"faltan columnas {faltantes}"])
    if df.empty:
        raise DatosInvalidos(["archivo vacío"])

    df = df[COLUMNAS[fuente]].copy()

    fechas = pd.to_datetime(df["date"], errors="coerce")
    if fechas.isna().any():
        errores.append(f"{int(fechas.isna().sum())} fechas inválidas")
    df["date"] = fechas

    if "store_nbr" in df:
        tienda = pd.to_numeric(df["store_nbr"], errors="coerce")
        if tienda.isna().any() or (tienda % 1 != 0).any():
            errores.append("store_nbr inválido")
        else:
            df["store_nbr"] = tienda.astype("int64")
            if tiendas is not None:
                desconocidas = set(df["store_nbr"]) - tiendas
                if desconocidas:
                    errores.append(f"tiendas desconocidas {sorted(desconocidas)[:5]}")

    if "family" in df:
        if df["family"].isna().any():
            errores.append("family nulo")
        elif familias is not None:
            desconocidas = set(df["family"]) - familias
            if desconocidas:
                errores.append(f"familias desconocidas {sorted(desconocidas)[:5]}")

    if fuente in NO_NEGATIVAS:
        col = NO_NEGATIVAS[fuente]
        valores = pd.to_numeric(df[col], errors="coerce")
        if valores.isna().any():
            errores.append(f"{col} nulo o no numérico")
        if (valores < 0).any():
            errores.append(f"{int((valores < 0).sum())} valores negativos en {col}")
        df[col] = valores

    if fuente == "petroleo":
        precio = pd.to_numeric(df["dcoilwtico"], errors="coerce")
        no_numericos = precio.isna() & df["dcoilwtico"].notna()
        if no_numericos.any():
            errores.append("dcoilwtico no numérico")
        if precio.isna().any():
            advertencias.append(f"{int(precio.isna().sum())} precios de petróleo vacíos")
        df["dcoilwtico"] = precio

    if fuente == "feriados":
        transferido = df["transferred"].astype(str).str.lower().map({"true": True, "false": False})
        if transferido.isna().any():
            errores.append("transferred inválido")
        df["transferred"] = transferido

    if LLAVES[fuente] and not errores:
        duplicados = df.duplicated(LLAVES[fuente]).sum()
        if duplicados:
            errores.append(f"{int(duplicados)} filas duplicadas")

    if fecha_esperada is not None and not errores:
        otras = df["date"] != fecha_esperada
        if otras.any():
            errores.append(f"{int(otras.sum())} filas con fecha distinta a {fecha_esperada.date()}")

    if errores:
        raise DatosInvalidos(errores)
    return df, advertencias
