"""Métricas técnicas y de negocio del pronóstico."""

from __future__ import annotations

import numpy as np


def wape(real: np.ndarray, pronostico: np.ndarray) -> float:
    """Error absoluto total sobre ventas totales. Funciona con ceros, a diferencia de MAPE."""
    total = np.sum(real)
    return float(np.sum(np.abs(real - pronostico)) / total) if total > 0 else float("nan")


def pinball(real: np.ndarray, P: np.ndarray, niveles: list[float]) -> float:
    """Pérdida cuantílica promedio sobre todos los niveles."""
    perdidas = []
    for j, q in enumerate(niveles):
        dif = real - P[:, j]
        perdidas.append(np.mean(np.maximum(q * dif, (q - 1) * dif)))
    return float(np.mean(perdidas))


def cobertura(real: np.ndarray, bajo: np.ndarray, alto: np.ndarray) -> float:
    """Proporción de ventas reales dentro del intervalo."""
    return float(np.mean((real >= bajo) & (real <= alto)))


def costo_negocio(real: np.ndarray, pedido: np.ndarray, costos: dict) -> float:
    """Costo de quiebre más costo de merma del pedido sugerido."""
    faltante = np.maximum(real - pedido, 0)
    sobrante = np.maximum(pedido - real, 0)
    return float(
        np.sum(costos["quiebre_por_unidad"] * faltante + costos["merma_por_unidad"] * sobrante)
    )

