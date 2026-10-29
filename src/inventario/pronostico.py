"""Preparación de datos, pronóstico, pedido sugerido y publicación por corte."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from inventario.config import Rutas, niveles
from inventario.features import filas_corte, construir_contexto, tabla_entrenamiento
from inventario.ingesta import FUENTES, _fecha_carpeta
from inventario.modelos import MODELOS, guardar_lgbm, pronosticar_modelo
from inventario.pedido import pedido_diario, ratio_critico

ID = ["corte", "date", "h", "store_nbr", "family"]


def columnas_cuantiles(niv: list[float]) -> list[str]:
    return [f"q{round(q * 100):02d}" for q in niv]


def _dir_corte(base: Path, corte) -> Path:
    return base / f"corte={pd.Timestamp(corte).date()}"


def tablas_modelo(rutas: Rutas, params: dict, corte) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tabla de entrenamiento y tabla de predicción, solo con datos disponibles al corte."""
    horizonte = params["producto"]["horizonte_dias"]
    m = params["modelo"]
    ctx = construir_contexto(rutas.raw, corte, horizonte, params["producto"]["dias_cierre"])
    entrenamiento = tabla_entrenamiento(
        ctx, corte, horizonte, m["n_cortes_entrenamiento"], m["paso_cortes_dias"],
        m["n_cortes_anio_anterior"],
    )
    prediccion = filas_corte(ctx, corte, horizonte, con_objetivo=False)
    return entrenamiento, prediccion


def preparar(rutas: Rutas, params: dict, corte) -> Path:
    entrenamiento, prediccion = tablas_modelo(rutas, params, corte)
    destino = _dir_corte(rutas.processed, corte)
    destino.mkdir(parents=True, exist_ok=True)
    entrenamiento.to_parquet(destino / "entrenamiento.parquet", index=False)
    prediccion.to_parquet(destino / "prediccion.parquet", index=False)
    return destino


def vigente_en(dir_modelos: Path, fecha) -> dict | None:
    """Última decisión de modelo vigente tomada en o antes de ``fecha``."""
    fecha = pd.Timestamp(fecha)
    decisiones = [
        p for p in (Path(dir_modelos) / "vigente").glob("fecha=*.json")
        if pd.Timestamp(p.stem.split("=", 1)[1]) <= fecha
    ]
    if not decisiones:
        return None
    return json.loads(max(decisiones).read_text())


def calcular(params: dict, entrenamiento, prediccion, vigente: str):
    """Pronóstico de todos los modelos y pedido del modelo vigente."""
    niv = niveles(params)
    qcols = columnas_cuantiles(niv)
    cobertura = params["producto"]["dias_cobertura_pedido"]
    partes, entrenados = [], {}
    for nombre in MODELOS:
        P, modelos = pronosticar_modelo(nombre, entrenamiento, prediccion, niv, params["modelo"]["lgbm"])
        if modelos:
            entrenados[nombre] = modelos
        tabla = prediccion[ID].copy()
        tabla["modelo"] = nombre
        tabla[qcols] = P.round(4)
        tabla["pedido"] = np.nan
        if nombre == vigente:
            en_cobertura = (tabla["h"] <= cobertura).to_numpy()
            tabla.loc[en_cobertura, "pedido"] = pedido_diario(
                P[en_cobertura], niv, params["costos"]
            ).round(4)
        partes.append(tabla)
    pronostico = pd.concat(partes, ignore_index=True)

    del_vigente = pronostico[(pronostico["modelo"] == vigente) & pronostico["pedido"].notna()]
    pedido = (
        del_vigente.groupby(["store_nbr", "family"], as_index=False)
        .agg(fecha_desde=("date", "min"), fecha_hasta=("date", "max"),
             pedido_sugerido=("pedido", "sum"), demanda_mediana=("q50", "sum"))
        .round({"pedido_sugerido": 4, "demanda_mediana": 4})
    )
    pedido.insert(0, "corte", prediccion["corte"].iloc[0])
    pedido["modelo"] = vigente
    return pronostico, pedido, entrenados


def hash_tabla(df: pd.DataFrame) -> str:
    texto = df.to_csv(index=False, float_format="%.4f", date_format="%Y-%m-%d")
    return hashlib.sha256(texto.encode()).hexdigest()


def _git(raiz: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-c", "safe.directory=*", *args], cwd=raiz, capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _datos_usados(dir_raw: Path, corte: pd.Timestamp) -> dict:
    resumen = {}
    for fuente in FUENTES:
        entregas = [
            _fecha_carpeta(c) for c in (dir_raw / fuente).glob("entrega=*") if _fecha_carpeta(c) <= corte
        ]
        archivos = sum(
            len(list(c.glob("*.parquet")))
            for c in (dir_raw / fuente).glob("entrega=*") if _fecha_carpeta(c) <= corte
        )
        resumen[fuente] = {"archivos": archivos, "ultima_entrega": str(max(entregas).date()) if entregas else None}
    return resumen


def modelo_vigente(rutas: Rutas, params: dict, corte) -> tuple[str, dict | None]:
    decision = vigente_en(rutas.modelos, corte)
    return (decision["modelo"] if decision else params["modelo"]["vigente_inicial"]), decision


def pronosticar(rutas: Rutas, params: dict, corte) -> dict:
    """Pronostica, calcula el pedido y publica en ``published/corte=AAAA-MM-DD``."""
    corte = pd.Timestamp(corte)
    origen = _dir_corte(rutas.processed, corte)
    entrenamiento = pd.read_parquet(origen / "entrenamiento.parquet")
    prediccion = pd.read_parquet(origen / "prediccion.parquet")
    vigente, decision = modelo_vigente(rutas, params, corte)

    pronostico, pedido, entrenados = calcular(params, entrenamiento, prediccion, vigente)
    for nombre, modelos in entrenados.items():
        guardar_lgbm(modelos, _dir_corte(rutas.modelos, corte) / nombre)

    raiz = Path(params.get("_raiz", "."))
    estado = _git(raiz, "status", "--porcelain", "--", "src", "config", "dags")
    manifiesto = {
        "corte": str(corte.date()),
        "modelo_vigente": vigente,
        "decision_vigente": decision,
        "ratio_critico": ratio_critico(
            params["costos"]["quiebre_por_unidad"], params["costos"]["merma_por_unidad"]
        ),
        "git_commit": _git(raiz, "rev-parse", "HEAD"),
        "codigo_con_cambios_sin_commit": bool(estado) if estado is not None else None,
        "params": {k: params[k] for k in ("producto", "modelo", "costos")},
        "datos": _datos_usados(rutas.raw, corte),
        "hash_pronostico": hash_tabla(pronostico),
        "hash_pedido": hash_tabla(pedido),
    }

    destino = _dir_corte(rutas.published, corte)
    temporal = destino.with_name(destino.name + ".tmp")
    shutil.rmtree(temporal, ignore_errors=True)
    temporal.mkdir(parents=True)
    pronostico.to_parquet(temporal / "pronostico.parquet", index=False)
    pedido.to_csv(temporal / "pedido.csv", index=False, date_format="%Y-%m-%d")
    (temporal / "manifest.json").write_text(json.dumps(manifiesto, indent=2, ensure_ascii=False))
    shutil.rmtree(destino, ignore_errors=True)
    temporal.rename(destino)
    return manifiesto


def reproducir(rutas: Rutas, params: dict, corte) -> dict:
    """Recalcula un pronóstico publicado desde ``data/raw`` y compara el hash."""
    corte = pd.Timestamp(corte)
    publicado = json.loads((_dir_corte(rutas.published, corte) / "manifest.json").read_text())
    entrenamiento, prediccion = tablas_modelo(rutas, params, corte)
    pronostico, pedido, _ = calcular(params, entrenamiento, prediccion, publicado["modelo_vigente"])
    nuevo = hash_tabla(pronostico)
    return {
        "corte": str(corte.date()),
        "hash_publicado": publicado["hash_pronostico"],
        "hash_reproducido": nuevo,
        "identico": nuevo == publicado["hash_pronostico"],
        "pedido_identico": hash_tabla(pedido) == publicado["hash_pedido"],
    }
