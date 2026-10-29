"""Comparación de modelos con validación temporal y criterio de reemplazo.

Todos los modelos se evalúan con los mismos cortes pasados (origen móvil). En
cada corte se entrena solo con datos anteriores y se mide contra ventas reales.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from inventario.config import Rutas, niveles
from inventario.features import construir_contexto, cortes_entrenamiento, filas_corte, tabla_entrenamiento
from inventario.metricas import cobertura, costo_negocio, pinball, wape
from inventario.modelos import MODELOS, pronosticar_modelo
from inventario.pedido import pedido_diario
from inventario.pronostico import vigente_en

METRICAS = ["costo", "wape", "pinball", "cobertura_80"]


def metricas_modelo(real, P, niv, en_cobertura, costos) -> dict:
    i50 = niv.index(0.5)
    pedido = pedido_diario(P[en_cobertura], niv, costos)
    return {
        "filas": int(len(real)),
        "ventas": float(real.sum()),
        "error_abs": float(np.abs(real - P[:, i50]).sum()),
        "wape": wape(real, P[:, i50]),
        "pinball": pinball(real, P, niv),
        "cobertura_80": cobertura(real, P[:, 0], P[:, -1]),
        "costo": costo_negocio(real[en_cobertura], pedido, costos),
    }


def backtest(rutas: Rutas, params: dict, fecha) -> pd.DataFrame:
    fecha = pd.Timestamp(fecha)
    horizonte = params["producto"]["horizonte_dias"]
    dias_pedido = params["producto"]["dias_cobertura_pedido"]
    m, e = params["modelo"], params["evaluacion"]
    niv = niveles(params)
    ctx = construir_contexto(rutas.raw, fecha, horizonte, params["producto"]["dias_cierre"])

    filas = []
    for corte in cortes_entrenamiento(fecha, horizonte, e["n_cortes_backtest"], e["paso_cortes_dias"]):
        entrenamiento = tabla_entrenamiento(
            ctx, corte, horizonte, m["n_cortes_entrenamiento"], m["paso_cortes_dias"],
            m["n_cortes_anio_anterior"],
        )
        prueba = filas_corte(ctx, corte, horizonte, con_objetivo=True)
        prueba = prueba[prueba["objetivo"].notna()].reset_index(drop=True)
        real = prueba["objetivo"].to_numpy()
        en_cobertura = (prueba["h"] <= dias_pedido).to_numpy()
        for nombre in MODELOS:
            P, _ = pronosticar_modelo(nombre, entrenamiento, prueba, niv, m["lgbm"])
            filas.append({"corte": corte, "modelo": nombre,
                          **metricas_modelo(real, P, niv, en_cobertura, params["costos"])})
    return pd.DataFrame(filas)


def agregar(tabla: pd.DataFrame) -> pd.DataFrame:
    g = tabla.groupby("modelo")
    return pd.DataFrame({
        "costo": g["costo"].sum(),
        "wape": g["error_abs"].sum() / g["ventas"].sum(),
        "pinball": g["pinball"].mean(),
        "cobertura_80": g["cobertura_80"].mean(),
    })


def seleccionar(agregado: pd.DataFrame, anterior: str | None, e: dict) -> tuple[str, str]:
    """Regla de reemplazo del modelo vigente.

    Un candidato reemplaza al vigente solo si reduce el costo de negocio al menos
    ``mejora_minima_costo`` y no empeora el WAPE más de ``tolerancia_wape``.
    """
    if anterior is None or anterior not in agregado.index:
        elegido = agregado["costo"].idxmin()
        return elegido, "No había modelo vigente: se elige el de menor costo de negocio."
    otros = agregado.drop(index=anterior)
    if otros.empty:
        return anterior, "No hay candidatos."
    candidato = otros["costo"].idxmin()
    c, v = agregado.loc[candidato], agregado.loc[anterior]
    mejora = 1 - c["costo"] / v["costo"] if v["costo"] > 0 else 0.0
    if mejora >= e["mejora_minima_costo"] and c["wape"] <= v["wape"] * (1 + e["tolerancia_wape"]):
        return candidato, f"{candidato} reduce el costo {mejora:.1%} sin empeorar el WAPE más de lo tolerado."
    return anterior, f"{candidato} no cumple el criterio (mejora de costo {mejora:.1%})."


def _tabla_md(df: pd.DataFrame) -> str:
    columnas = [str(c) for c in df.columns]
    lineas = ["| " + " | ".join(columnas) + " |", "|" + "---|" * len(columnas)]
    for fila in df.itertuples(index=False):
        celdas = [f"{v:.4f}" if isinstance(v, float) else str(v) for v in fila]
        lineas.append("| " + " | ".join(celdas) + " |")
    return "\n".join(lineas)


def necesita_evaluacion(rutas: Rutas, params: dict, fecha) -> bool:
    fecha = pd.Timestamp(fecha)
    anterior = vigente_en(rutas.modelos, fecha - pd.Timedelta(days=1))
    if anterior is None:
        return True
    dias = (fecha - pd.Timestamp(anterior["fecha"])).days
    return dias >= 7 * params["evaluacion"]["evaluar_cada_semanas"]


def evaluar(rutas: Rutas, params: dict, fecha) -> dict:
    fecha = pd.Timestamp(fecha)
    tabla = backtest(rutas, params, fecha)
    agregado = agregar(tabla)
    previo = vigente_en(rutas.modelos, fecha - pd.Timedelta(days=1))
    anterior = previo["modelo"] if previo else None
    elegido, motivo = seleccionar(agregado, anterior, params["evaluacion"])

    decision = {
        "fecha": str(fecha.date()),
        "modelo": elegido,
        "anterior": anterior,
        "motivo": motivo,
        "metricas": agregado.round(6).to_dict(orient="index"),
    }
    destino = Path(rutas.modelos) / "vigente" / f"fecha={fecha.date()}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(decision, indent=2, ensure_ascii=False))

    reporte = Path(rutas.evaluacion) / f"fecha={fecha.date()}"
    reporte.mkdir(parents=True, exist_ok=True)
    tabla.to_csv(reporte / "metricas_por_corte.csv", index=False, date_format="%Y-%m-%d")
    texto = [
        f"# Comparación de modelos al {fecha.date()}",
        "",
        "Validación temporal con origen móvil; mismos cortes para todos los modelos.",
        "",
        _tabla_md(agregado.reset_index()),
        "",
        f"**Modelo vigente:** {elegido}. {motivo}",
    ]
    (reporte / "comparacion.md").write_text("\n".join(texto), encoding="utf-8")
    return decision
