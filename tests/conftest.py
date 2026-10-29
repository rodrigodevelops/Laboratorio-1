"""Datos sintéticos compartidos por las pruebas.

Tienen la misma estructura que los archivos de Kaggle, pero son pequeños: los
datos reales no están en Git y el CI no tiene acceso a ellos.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

TIENDAS = [1, 2]
FAMILIAS = ["BEVERAGES", "GROCERY I"]


@pytest.fixture
def dir_kaggle(tmp_path: Path) -> Path:
    d = tmp_path / "kaggle"
    d.mkdir()
    rng = np.random.default_rng(0)
    fechas = pd.date_range("2016-05-01", "2017-02-28")
    # Igual que en Kaggle, no hay ventas el 25 de diciembre.
    fechas_venta = fechas[~((fechas.month == 12) & (fechas.day == 25))]

    train = pd.DataFrame(
        [(f, t, fam) for f in fechas_venta for t in TIENDAS for fam in FAMILIAS],
        columns=["date", "store_nbr", "family"],
    )
    train["sales"] = rng.integers(1, 50, len(train)).astype(float)
    train["onpromotion"] = rng.integers(0, 3, len(train))
    train.insert(0, "id", range(len(train)))
    train.to_csv(d / "train.csv", index=False)

    trans = pd.DataFrame(
        [(f, t) for f in fechas_venta for t in TIENDAS], columns=["date", "store_nbr"]
    )
    trans["transactions"] = rng.integers(100, 500, len(trans))
    trans.drop(trans.index[::11]).to_csv(d / "transactions.csv", index=False)

    habiles = fechas[fechas.dayofweek < 5]
    oil = pd.DataFrame({"date": habiles, "dcoilwtico": rng.uniform(40, 60, len(habiles))})
    oil.loc[3, "dcoilwtico"] = np.nan
    oil.to_csv(d / "oil.csv", index=False)

    pd.DataFrame(
        {
            "date": ["2016-01-01", "2016-12-25", "2017-01-01", "2017-12-25"],
            "type": "Holiday",
            "locale": "National",
            "locale_name": "Ecuador",
            "description": ["Primer dia", "Navidad", "Primer dia", "Navidad"],
            "transferred": False,
        }
    ).to_csv(d / "holidays_events.csv", index=False)

    pd.DataFrame(
        {"store_nbr": TIENDAS, "city": "Quito", "state": "Pichincha", "type": "D", "cluster": 13}
    ).to_csv(d / "stores.csv", index=False)

    # Archivo de Kaggle que el laboratorio no usa: debe ignorarse sin fallar.
    (d / "test.csv").write_text("id,date\n")
    return d



@pytest.fixture
def proyecto(tmp_path: Path, dir_kaggle: Path) -> Path:
    """Repo mínimo con config/params.yaml, carga histórica y bandeja generadas."""
    from inventario.bandeja import ConfigBandeja, generar

    raiz = tmp_path / "repo"
    (raiz / "config").mkdir(parents=True)
    params = {
        "rutas": {
            "historico": "data/historico", "bandeja": "data/bandeja", "raw": "data/raw",
            "processed": "data/processed", "published": "data/published", "modelos": "models",
            "evaluacion": "data/evaluacion", "seguimiento": "data/seguimiento",
        },
        "costos": {"quiebre_por_unidad": 3.0, "merma_por_unidad": 1.0},
        "producto": {"dia_corte": 0, "horizonte_dias": 14, "dias_cobertura_pedido": 7,
                     "cuantiles": [0.1, 0.25, 0.5, 0.75, 0.9],
                     "dias_cierre": ["12-25", "01-01"]},
        "modelo": {
            "vigente_inicial": "baseline", "n_cortes_entrenamiento": 4, "n_cortes_anio_anterior": 0, "paso_cortes_dias": 7,
            "lgbm": {"n_estimators": 20, "learning_rate": 0.1, "num_leaves": 7,
                     "min_data_in_leaf": 5, "feature_fraction": 0.8, "num_threads": 1, "semilla": 1},
        },
        "evaluacion": {"n_cortes_backtest": 2, "paso_cortes_dias": 7, "evaluar_cada_semanas": 4,
                       "mejora_minima_costo": 0.02, "tolerancia_wape": 0.05},
        "validacion": {"retraso_esperado_ventas": 1},
        "bandeja": {"fecha_inicio": "2017-01-01", "fecha_fin": "2017-02-15", "semilla": 3,
                    "prob_correccion_ventas": 0.2},
    }
    (raiz / "config" / "params.yaml").write_text(yaml.safe_dump(params))
    generar(dir_kaggle, raiz / "data/historico", raiz / "data/bandeja", ConfigBandeja(**params["bandeja"]))
    return raiz
