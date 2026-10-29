"""Lectura de la configuración versionada (config/params.yaml)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml


def cargar_params(ruta: str | Path = "config/params.yaml") -> dict:
    with open(ruta, encoding="utf-8") as f:
        params = yaml.safe_load(f)
    params["_raiz"] = str(Path(ruta).resolve().parent.parent)
    return params


@dataclass(frozen=True)
class Rutas:
    historico: Path
    bandeja: Path
    raw: Path
    processed: Path
    published: Path
    modelos: Path
    evaluacion: Path
    seguimiento: Path

    @classmethod
    def desde_params(cls, params: dict) -> "Rutas":
        raiz = Path(params.get("_raiz", "."))
        return cls(**{k: raiz / v for k, v in params["rutas"].items()})


def corte_historico(params: dict) -> pd.Timestamp:
    """Fecha de entrega asignada a la carga histórica."""
    return pd.Timestamp(params["bandeja"]["fecha_inicio"]) - pd.Timedelta(days=1)


def es_dia_de_corte(fecha, params: dict) -> bool:
    return pd.Timestamp(fecha).dayofweek == params["producto"]["dia_corte"]


def niveles(params: dict) -> list[float]:
    return sorted(float(q) for q in params["producto"]["cuantiles"])
