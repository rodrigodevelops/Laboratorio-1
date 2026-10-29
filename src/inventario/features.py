"""Construcción de la tabla del modelo con información disponible en cada corte.

Para un corte ``c`` se pronostican los días ``c+1 .. c+H``. Las features solo
miran ventas hasta ``c-1`` (las del día ``c`` aún no se conocen), transacciones
hasta ``c-3`` y petróleo hasta ``c-2``, que es lo que normalmente ya llegó a la
bandeja en el momento del corte. Las promociones y feriados del día objetivo sí
se usan porque se publican por adelantado.

Los días de cierre (25 de diciembre y 1 de enero) se tratan como sin dato: no
entran en las features ni como objetivo de entrenamiento, porque sus ventas
casi nulas distorsionan los promedios recientes de todas las series.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from inventario.ingesta import leer_vista

DOW_COLS = [f"dow_{i}" for i in range(1, 9)]
CATEGORICAS = ["store_nbr", "family_cod", "tipo_cod", "cluster"]
FEATURES = [
    "h", "dow", "dia", "mes", "feriado", "promo", "promo_m28",
    "ult", "m7", "m28", "m91", "ceros28", "dow4", *DOW_COLS,
    "trans7", "petroleo", *CATEGORICAS,
]


@dataclass
class Contexto:
    corte: pd.Timestamp
    fechas: pd.DatetimeIndex
    series: pd.DataFrame
    ventas: np.ndarray
    promos: np.ndarray
    feriados: np.ndarray
    trans: np.ndarray
    petroleo: np.ndarray

    def pos(self, fecha) -> int:
        return int((pd.Timestamp(fecha) - self.fechas[0]).days)


def _matriz(df, columna, indice, fechas, llaves) -> np.ndarray:
    tabla = df.set_index([*llaves, "date"])[columna].unstack("date")
    return tabla.reindex(index=indice, columns=fechas).to_numpy(dtype=float)


def _feriados_por_tienda(feriados, tiendas, fechas) -> np.ndarray:
    activos = feriados[(~feriados["transferred"].astype(bool)) & (feriados["type"] != "Work Day")]
    matriz = np.zeros((len(tiendas), len(fechas)), dtype=bool)
    for fila in activos.itertuples(index=False):
        p = (fila.date - fechas[0]).days
        if not 0 <= p < len(fechas):
            continue
        if fila.locale == "National":
            matriz[:, p] = True
        elif fila.locale == "Regional":
            matriz[(tiendas["state"] == fila.locale_name).to_numpy(), p] = True
        else:
            matriz[(tiendas["city"] == fila.locale_name).to_numpy(), p] = True
    return matriz


def construir_contexto(dir_raw: Path, corte, horizonte: int, dias_cierre=("12-25", "01-01")) -> Contexto:
    """Carga la vista de todas las fuentes al ``corte`` y la convierte en matrices."""
    corte = pd.Timestamp(corte)
    ventas = leer_vista(dir_raw, "ventas", corte)
    promos = leer_vista(dir_raw, "promociones", corte)
    trans = leer_vista(dir_raw, "transacciones", corte)
    petroleo = leer_vista(dir_raw, "petroleo", corte)
    feriados = leer_vista(dir_raw, "feriados", corte)
    tiendas = pd.read_parquet(Path(dir_raw) / "tiendas.parquet").sort_values("store_nbr")
    familias = sorted(pd.read_parquet(Path(dir_raw) / "familias.parquet")["family"])

    fechas = pd.date_range(ventas["date"].min(), corte + pd.Timedelta(days=horizonte))
    series = pd.MultiIndex.from_product(
        [tiendas["store_nbr"].astype(int), familias], names=["store_nbr", "family"]
    ).to_frame(index=False)
    indice = pd.MultiIndex.from_frame(series)
    llaves = ["store_nbr", "family"]

    info = tiendas.set_index("store_nbr")
    tipos = {t: i for i, t in enumerate(sorted(info["type"].astype(str).unique()))}
    series["family_cod"] = series["family"].map({f: i for i, f in enumerate(familias)})
    series["tipo_cod"] = series["store_nbr"].map(info["type"].astype(str).map(tipos))
    series["cluster"] = series["store_nbr"].map(info["cluster"]).astype(int)

    fila_tienda = series["store_nbr"].map(
        {t: i for i, t in enumerate(tiendas["store_nbr"].astype(int))}
    ).to_numpy()
    trans_tienda = trans.set_index(["store_nbr", "date"])["transactions"].unstack("date")
    trans_tienda = trans_tienda.reindex(index=tiendas["store_nbr"], columns=fechas).to_numpy(float)
    oil = np.array(petroleo.set_index("date")["dcoilwtico"].reindex(fechas).ffill(), dtype=float)

    matriz_ventas = _matriz(ventas, "sales", indice, fechas, llaves)
    cierre = np.isin(fechas.strftime("%m-%d"), list(dias_cierre))
    matriz_ventas[:, cierre] = np.nan

    return Contexto(
        corte=corte,
        fechas=fechas,
        series=series,
        ventas=matriz_ventas,
        promos=_matriz(promos, "onpromotion", indice, fechas, llaves),
        feriados=_feriados_por_tienda(feriados, tiendas.reset_index(drop=True), fechas)[fila_tienda],
        trans=trans_tienda[fila_tienda],
        petroleo=oil,
    )


def _ventana(matriz: np.ndarray, pc: int, desde: int, hasta: int) -> np.ndarray:
    """Columnas de los días ``c-desde .. c-hasta`` (inclusive)."""
    return matriz[:, max(pc - desde, 0) : max(pc - hasta + 1, 0)]


def _media(m: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(m, axis=1) if m.shape[1] else np.full(m.shape[0], np.nan)


def _ultimo_observado(m: np.ndarray) -> np.ndarray:
    """Último valor no nulo de cada fila (NaN si la fila no tiene datos)."""
    observado = ~np.isnan(m)
    if m.shape[1] == 0:
        return np.full(m.shape[0], np.nan)
    ultimo = m.shape[1] - 1 - np.argmax(observado[:, ::-1], axis=1)
    valores = m[np.arange(len(m)), ultimo]
    return np.where(observado.any(axis=1), valores, np.nan)


def filas_corte(ctx: Contexto, corte, horizonte: int, con_objetivo: bool) -> pd.DataFrame:
    """Una fila por serie y día del horizonte, con features calculadas al ``corte``."""
    corte = pd.Timestamp(corte)
    pc = ctx.pos(corte)
    if pc < 1:
        raise ValueError(f"No hay historia antes del corte {corte.date()}")
    V = ctx.ventas
    w28 = _ventana(V, pc, 28, 1)
    observados = np.maximum((~np.isnan(w28)).sum(axis=1), 1)
    w56 = _ventana(V, pc, 56, 1)
    dias_w56 = ctx.fechas[max(pc - 56, 0) : pc].dayofweek

    base = {
        "store_nbr": ctx.series["store_nbr"].to_numpy(),
        "family": ctx.series["family"].to_numpy(),
        "family_cod": ctx.series["family_cod"].to_numpy(),
        "tipo_cod": ctx.series["tipo_cod"].to_numpy(),
        "cluster": ctx.series["cluster"].to_numpy(),
        "ult": _ultimo_observado(_ventana(V, pc, 7, 1)),
        "m7": _media(_ventana(V, pc, 7, 1)),
        "m28": _media(w28),
        "m91": _media(_ventana(V, pc, 91, 1)),
        "ceros28": (w28 == 0).sum(axis=1) / observados,
        "suma56": np.nansum(w56, axis=1),
        "nivel": np.log1p(np.nan_to_num(_media(w28), nan=0.0)),
        "promo_m28": _media(_ventana(ctx.promos, pc, 28, 1)),
        "trans7": _media(_ventana(ctx.trans, pc, 9, 3)),
        "petroleo": ctx.petroleo[pc - 2] if pc >= 2 else np.nan,
    }

    bloques = []
    for h in range(1, horizonte + 1):
        objetivo = corte + pd.Timedelta(days=h)
        pt = pc + h
        columnas = np.flatnonzero(dias_w56 == objetivo.dayofweek)[-8:]
        mismos = np.full((len(V), 8), np.nan)
        if len(columnas):
            mismos[:, 8 - len(columnas) :] = w56[:, columnas]
        bloque = pd.DataFrame(base)
        bloque.insert(0, "date", objetivo)
        bloque.insert(0, "h", h)
        bloque.insert(0, "corte", corte)
        bloque["dow"] = objetivo.dayofweek
        bloque["dia"] = objetivo.day
        bloque["mes"] = objetivo.month
        bloque["feriado"] = ctx.feriados[:, pt].astype(int)
        bloque["promo"] = ctx.promos[:, pt]
        bloque[DOW_COLS] = mismos
        bloque["dow4"] = _media(mismos[:, 4:])
        if con_objetivo:
            bloque["objetivo"] = V[:, pt]
        bloques.append(bloque)
    return pd.concat(bloques, ignore_index=True)


def cortes_entrenamiento(corte, horizonte: int, n: int, paso: int) -> list[pd.Timestamp]:
    """Cortes pasados cuyos objetivos ya se conocen en ``corte`` (hasta ``corte-1``)."""
    corte = pd.Timestamp(corte)
    return [corte - pd.Timedelta(days=horizonte + 1 + paso * k) for k in range(n)]


def cortes_anio_anterior(corte, n: int, paso: int) -> list[pd.Timestamp]:
    """Cortes alrededor de la misma semana del año anterior (52 semanas atrás).

    Le muestran al modelo cómo cambia la demanda en esta época del año, por
    ejemplo la caída de enero después de diciembre. Sus objetivos ya se conocen.
    """
    corte = pd.Timestamp(corte) - pd.Timedelta(days=364)
    return [corte + pd.Timedelta(days=paso * j) for j in range(-(n // 2), n - n // 2)]


def tabla_entrenamiento(
    ctx: Contexto, corte, horizonte: int, n: int, paso: int, n_anio_anterior: int = 0
) -> pd.DataFrame:
    cortes = cortes_entrenamiento(corte, horizonte, n, paso)
    cortes += [c for c in cortes_anio_anterior(corte, n_anio_anterior, paso) if c not in cortes]
    partes = []
    for c in sorted(cortes):
        if ctx.pos(c) >= 1:
            partes.append(filas_corte(ctx, c, horizonte, con_objetivo=True))
    if not partes:
        raise ValueError("No hay suficiente historia para entrenar")
    tabla = pd.concat(partes, ignore_index=True)
    return tabla[tabla["objetivo"].notna()].reset_index(drop=True)
