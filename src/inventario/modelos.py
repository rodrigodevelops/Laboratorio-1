"""Modelos de pronóstico por cuantiles.

- ``baseline``: cuantiles empíricos de las últimas 8 ventas del mismo día de la
  semana. Es simple, no se entrena y sirve de piso de comparación.
- ``lgbm``: un LightGBM global (todas las series) por cuantil. Aprende la
  diferencia entre ``log1p(ventas)`` y el nivel reciente de la serie
  (``log1p`` de la media de 28 días). Así un mismo patrón sirve para series
  grandes y pequeñas. Los cuantiles son invariantes a transformaciones monótonas
  y a sumar una constante conocida, así que ``expm1(pred + nivel)`` es el
  cuantil de las ventas.

Reglas comunes: los cuantiles se ordenan para que no se crucen, y las series sin
ventas en las últimas 8 semanas reciben pronóstico 0.
"""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from inventario.features import CATEGORICAS, DOW_COLS, FEATURES

MODELOS = ("baseline", "lgbm")


def _ajustar(P: np.ndarray, tabla: pd.DataFrame) -> np.ndarray:
    P = np.sort(np.nan_to_num(P, nan=0.0).clip(min=0), axis=1)
    P[tabla["suma56"].to_numpy() == 0] = 0.0
    return P


def predecir_baseline(tabla: pd.DataFrame, niveles: list[float]) -> np.ndarray:
    valores = tabla[DOW_COLS].to_numpy(dtype=float)
    P = np.full((len(tabla), len(niveles)), np.nan)
    con_datos = ~np.all(np.isnan(valores), axis=1)
    if con_datos.any():
        P[con_datos] = np.nanquantile(valores[con_datos], niveles, axis=1).T
    return _ajustar(P, tabla)


def entrenar_lgbm(tabla: pd.DataFrame, niveles: list[float], params: dict) -> dict[float, lgb.Booster]:
    datos = lgb.Dataset(
        tabla[FEATURES],
        label=np.log1p(tabla["objetivo"].clip(lower=0)) - tabla["nivel"],
        categorical_feature=CATEGORICAS,
        free_raw_data=False,
    )
    base = {
        "objective": "quantile",
        "learning_rate": params["learning_rate"],
        "num_leaves": params["num_leaves"],
        "min_data_in_leaf": params["min_data_in_leaf"],
        "feature_fraction": params["feature_fraction"],
        "num_threads": params["num_threads"],
        "seed": params["semilla"],
        "deterministic": True,
        "force_row_wise": True,
        "verbose": -1,
    }
    return {
        q: lgb.train({**base, "alpha": q}, datos, num_boost_round=params["n_estimators"])
        for q in niveles
    }


def predecir_lgbm(modelos: dict[float, lgb.Booster], tabla: pd.DataFrame) -> np.ndarray:
    X = tabla[FEATURES]
    nivel = tabla["nivel"].to_numpy()
    P = np.column_stack([np.expm1(modelos[q].predict(X) + nivel) for q in sorted(modelos)])
    return _ajustar(P, tabla)


def guardar_lgbm(modelos: dict[float, lgb.Booster], directorio: Path) -> None:
    directorio.mkdir(parents=True, exist_ok=True)
    for q, booster in modelos.items():
        booster.save_model(str(directorio / f"lgbm_q{q:.2f}.txt"))
    (directorio / "niveles.json").write_text(json.dumps(sorted(modelos)))


def cargar_lgbm(directorio: Path) -> dict[float, lgb.Booster]:
    niveles = json.loads((directorio / "niveles.json").read_text())
    return {q: lgb.Booster(model_file=str(directorio / f"lgbm_q{q:.2f}.txt")) for q in niveles}


def pronosticar_modelo(nombre, entrenamiento, prediccion, niveles, params_lgbm):
    """Devuelve (matriz de cuantiles, modelos entrenados o None)."""
    if nombre == "baseline":
        return predecir_baseline(prediccion, niveles), None
    if nombre == "lgbm":
        modelos = entrenar_lgbm(entrenamiento, niveles, params_lgbm)
        return predecir_lgbm(modelos, prediccion), modelos
    raise ValueError(f"Modelo desconocido: {nombre}")
