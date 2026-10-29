"""Generación de la carga histórica y de la bandeja simulada del sistema de origen.

La bandeja imita los archivos que la empresa entrega día a día. Cada archivo vive
en una carpeta ``entrega=AAAA-MM-DD``: esa fecha es el momento en que el dato queda
disponible (``available_at``). El flujo solo puede leer entregas con fecha menor o
igual a su fecha de ejecución.

Regla de la carga histórica: contiene todo lo que ya se había entregado al cierre
del día anterior a ``fecha_inicio``. Todo lo demás llega por la bandeja.

Este script es el único que escribe en la bandeja. El producto la trata como un
sistema externo de solo lectura.

Uso:
    PYTHONPATH=src python -m inventario.bandeja --config config/params.yaml
"""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

FUENTES_DIARIAS = ("ventas", "promociones", "transacciones", "petroleo")

COLUMNAS = {
    "ventas": ["date", "store_nbr", "family", "sales"],
    "promociones": ["date", "store_nbr", "family", "onpromotion"],
}

# Cada proceso aleatorio usa su propia semilla derivada, para que cambiar uno
# (por ejemplo, la probabilidad de corrección) no altere los demás.
_SUBSEMILLA = {"transacciones": 1, "correcciones": 2, "valores_corregidos": 3}


@dataclass(frozen=True)
class ConfigBandeja:
    fecha_inicio: pd.Timestamp
    fecha_fin: pd.Timestamp
    semilla: int = 42
    retraso_ventas: int = 1
    retraso_transacciones: int = 2
    retraso_petroleo: int = 1
    anticipacion_promociones: int = 14
    prob_retraso_extra_transacciones: float = 0.05
    retraso_extra_max: int = 3
    prob_correccion_ventas: float = 0.02
    correccion_min_dias: int = 3
    correccion_max_dias: int = 10
    fraccion_filas_corregidas: float = 0.05
    variacion_correccion: float = 0.2
    publicacion_feriados: str = "12-01"

    def __post_init__(self) -> None:
        object.__setattr__(self, "fecha_inicio", pd.Timestamp(self.fecha_inicio))
        object.__setattr__(self, "fecha_fin", pd.Timestamp(self.fecha_fin))
        if self.fecha_fin < self.fecha_inicio:
            raise ValueError("fecha_fin debe ser igual o posterior a fecha_inicio")
        if not 1 <= self.correccion_min_dias <= self.correccion_max_dias:
            raise ValueError("Rango de días de corrección inválido")
        if self.retraso_extra_max < 1:
            raise ValueError("retraso_extra_max debe ser al menos 1")

    @property
    def corte_historico(self) -> pd.Timestamp:
        """Último día cuyas entregas forman parte de la carga histórica."""
        return self.fecha_inicio - pd.Timedelta(days=1)

    @classmethod
    def desde_yaml(cls, ruta: str | Path) -> "ConfigBandeja":
        with open(ruta, encoding="utf-8") as f:
            return cls(**yaml.safe_load(f)["bandeja"])


def _rng(cfg: ConfigBandeja, proceso: str, *extra: int) -> np.random.Generator:
    return np.random.default_rng([cfg.semilla, _SUBSEMILLA[proceso], *extra])


def cargar_kaggle(dir_kaggle: str | Path) -> dict[str, pd.DataFrame]:
    """Lee solo los archivos de Kaggle que usa el laboratorio (ignora test.csv, etc.)."""
    d = Path(dir_kaggle)
    train = pd.read_csv(d / "train.csv", parse_dates=["date"])
    return {
        "ventas": train[COLUMNAS["ventas"]],
        "promociones": train[COLUMNAS["promociones"]],
        "transacciones": pd.read_csv(d / "transactions.csv", parse_dates=["date"]),
        "petroleo": pd.read_csv(d / "oil.csv", parse_dates=["date"]),
        "feriados": pd.read_csv(d / "holidays_events.csv", parse_dates=["date"]),
        "tiendas": pd.read_csv(d / "stores.csv"),
    }


def _fechas(df: pd.DataFrame, fecha_fin: pd.Timestamp) -> pd.DatetimeIndex:
    fechas = df.loc[df["date"] <= fecha_fin, "date"].drop_duplicates().sort_values()
    return pd.DatetimeIndex(fechas)


def _plan(fuente: str, claves, entregas, version: int = 1) -> pd.DataFrame:
    return pd.DataFrame(
        {"fuente": fuente, "clave": claves, "entrega": entregas, "version": version}
    )


def planificar_entregas(datos: dict[str, pd.DataFrame], cfg: ConfigBandeja) -> pd.DataFrame:
    """Devuelve una fila por archivo a entregar.

    Columnas: ``fuente``, ``clave`` (fecha del dato; para feriados, 1 de enero del
    año del calendario), ``entrega`` (fecha en que queda disponible) y ``version``
    (1 = entrega original, 2 = corrección que reemplaza el archivo completo).
    """
    un_dia = pd.Timedelta(days=1)
    planes = []

    d = _fechas(datos["ventas"], cfg.fecha_fin)
    ventas = _plan("ventas", d, d + cfg.retraso_ventas * un_dia)
    planes.append(ventas)

    d = _fechas(datos["promociones"], cfg.fecha_fin)
    planes.append(_plan("promociones", d, d - cfg.anticipacion_promociones * un_dia))

    d = _fechas(datos["petroleo"], cfg.fecha_fin)
    planes.append(_plan("petroleo", d, d + cfg.retraso_petroleo * un_dia))

    d = _fechas(datos["transacciones"], cfg.fecha_fin)
    rng = _rng(cfg, "transacciones")
    tarde = rng.random(len(d)) < cfg.prob_retraso_extra_transacciones
    extra = np.where(tarde, rng.integers(1, cfg.retraso_extra_max + 1, len(d)), 0)
    atraso = pd.to_timedelta(cfg.retraso_transacciones + extra, unit="D")
    planes.append(_plan("transacciones", d, d + atraso))

    # Feriados: un calendario por año, publicado antes de que empiece el año.
    anios = sorted(
        a for a in datos["feriados"]["date"].dt.year.unique() if a <= cfg.fecha_fin.year
    )
    claves = pd.DatetimeIndex([pd.Timestamp(year=int(a), month=1, day=1) for a in anios])
    entregas = pd.DatetimeIndex(
        [pd.Timestamp(f"{int(a) - 1}-{cfg.publicacion_feriados}") for a in anios]
    )
    planes.append(_plan("feriados", claves, entregas))

    # Correcciones: solo sobre ventas que llegan por la bandeja.
    rng = _rng(cfg, "correcciones")
    candidatas = ventas[ventas["entrega"] > cfg.corte_historico]
    elegidas = candidatas[rng.random(len(candidatas)) < cfg.prob_correccion_ventas]
    dias = rng.integers(cfg.correccion_min_dias, cfg.correccion_max_dias + 1, len(elegidas))
    planes.append(
        elegidas.assign(
            entrega=elegidas["entrega"] + pd.to_timedelta(dias, unit="D").to_numpy(),
            version=2,
        )
    )

    plan = pd.concat(planes, ignore_index=True)
    return plan.sort_values(["fuente", "clave", "version"], ignore_index=True)


def aplicar_correccion(filas: pd.DataFrame, cfg: ConfigBandeja, clave: pd.Timestamp) -> pd.DataFrame:
    """Modifica una fracción de las ventas positivas del día, de forma reproducible."""
    corregido = filas.copy()
    con_venta = np.flatnonzero(corregido["sales"].to_numpy() > 0)
    if len(con_venta) == 0:
        return corregido
    rng = _rng(cfg, "valores_corregidos", clave.toordinal())
    n = max(1, round(cfg.fraccion_filas_corregidas * len(con_venta)))
    idx = rng.choice(con_venta, size=n, replace=False)
    factor = rng.uniform(1 - cfg.variacion_correccion, 1 + cfg.variacion_correccion, n)
    col = corregido.columns.get_loc("sales")
    nuevos = (corregido.iloc[idx, col].to_numpy() * factor).clip(min=0).round(3)
    corregido.iloc[idx, col] = nuevos
    return corregido


def _a_csv(df: pd.DataFrame, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ruta, index=False, date_format="%Y-%m-%d")


def _recrear(directorio: Path) -> None:
    if directorio.exists():
        shutil.rmtree(directorio)
    directorio.mkdir(parents=True)


def _llave(df: pd.DataFrame, fuente: str) -> pd.Series:
    return df["date"].dt.year if fuente == "feriados" else df["date"]


def _escribir_historico(datos, plan: pd.DataFrame, destino: Path) -> None:
    for fuente in (*FUENTES_DIARIAS, "feriados"):
        claves = plan.loc[plan["fuente"] == fuente, "clave"]
        if fuente == "feriados":
            claves = claves.dt.year
        df = datos[fuente]
        _a_csv(df[_llave(df, fuente).isin(claves)], destino / f"{fuente}.csv")
    _a_csv(datos["tiendas"], destino / "tiendas.csv")


def _escribir_bandeja(datos, plan: pd.DataFrame, destino: Path, cfg: ConfigBandeja) -> None:
    for fuente, plan_fuente in plan.groupby("fuente"):
        df = datos[fuente]
        llave = _llave(df, fuente)
        claves = plan_fuente["clave"].dt.year if fuente == "feriados" else plan_fuente["clave"]
        mascara = llave.isin(claves)
        grupos = dict(tuple(df[mascara].groupby(llave[mascara])))

        for fila in plan_fuente.itertuples(index=False):
            es_feriado = fuente == "feriados"
            filas = grupos[fila.clave.year if es_feriado else fila.clave]
            if fila.version > 1:
                filas = aplicar_correccion(filas, cfg, fila.clave)
            etiqueta = fila.clave.year if es_feriado else fila.clave.date()
            carpeta = destino / fuente / f"entrega={fila.entrega.date()}"
            _a_csv(filas, carpeta / f"{fuente}_{etiqueta}.csv")


def generar(
    dir_kaggle: str | Path,
    dir_historico: str | Path,
    dir_bandeja: str | Path,
    cfg: ConfigBandeja,
) -> pd.DataFrame:
    """Genera la carga histórica y la bandeja desde cero. Devuelve el plan de entregas.

    Es idempotente: borra y vuelve a crear ambos directorios de salida, así que
    ejecutarlo varias veces con la misma configuración produce los mismos archivos.
    """
    entrada = Path(dir_kaggle).resolve()
    historico = Path(dir_historico).resolve()
    bandeja = Path(dir_bandeja).resolve()
    for salida in (historico, bandeja):
        if salida.is_relative_to(entrada) or entrada.is_relative_to(salida):
            raise ValueError(f"La salida {salida} no puede contener ni estar dentro de {entrada}")

    datos = cargar_kaggle(entrada)
    plan = planificar_entregas(datos, cfg)

    _recrear(historico)
    _recrear(bandeja)
    es_historico = plan["entrega"] <= cfg.corte_historico
    _escribir_historico(datos, plan[es_historico], historico)
    _escribir_bandeja(datos, plan[~es_historico], bandeja, cfg)
    return plan


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Genera la carga histórica y la bandeja.")
    parser.add_argument("--config", default="config/params.yaml")
    parser.add_argument("--kaggle", default="data/kaggle")
    parser.add_argument("--historico", default="data/historico")
    parser.add_argument("--bandeja", default="data/bandeja")
    args = parser.parse_args(argv)

    cfg = ConfigBandeja.desde_yaml(args.config)
    plan = generar(args.kaggle, args.historico, args.bandeja, cfg)

    en_bandeja = plan[plan["entrega"] > cfg.corte_historico]
    print(f"Corte histórico: {cfg.corte_historico.date()}")
    print("Archivos en la bandeja por fuente:")
    print(en_bandeja.groupby("fuente").size().to_string())
    print(f"Correcciones de ventas: {(en_bandeja['version'] > 1).sum()}")


if __name__ == "__main__":
    main()