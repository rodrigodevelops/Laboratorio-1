"""Pedido sugerido a partir del pronóstico por cuantiles (modelo newsvendor).

Si quedarse sin producto cuesta ``Cu`` por unidad y la merma cuesta ``Co``, la
cantidad que minimiza el costo esperado es el cuantil ``Cu / (Cu + Co)`` de la
demanda. Los costos vienen de ``config/params.yaml``: cambiarlos cambia el pedido
sin reentrenar, porque el cuantil se interpola entre los niveles pronosticados.
"""

from __future__ import annotations

import numpy as np


def ratio_critico(costo_quiebre: float, costo_merma: float) -> float:
    """Cuantil de la demanda que minimiza el costo esperado (newsvendor)."""
    if costo_quiebre <= 0 or costo_merma <= 0:
        raise ValueError("Los costos deben ser positivos")
    return costo_quiebre / (costo_quiebre + costo_merma)


def cuantil_interpolado(P: np.ndarray, niveles: list[float], tau: float) -> np.ndarray:
    """Interpola linealmente el cuantil ``tau`` en cada fila de ``P``.

    Fuera del rango de niveles se usa el nivel extremo más cercano.
    """
    niveles = np.asarray(niveles, dtype=float)
    tau = float(np.clip(tau, niveles[0], niveles[-1]))
    j = int(np.clip(np.searchsorted(niveles, tau), 1, len(niveles) - 1))
    peso = (tau - niveles[j - 1]) / (niveles[j] - niveles[j - 1])
    return P[:, j - 1] + peso * (P[:, j] - P[:, j - 1])


def pedido_diario(P: np.ndarray, niveles: list[float], costos: dict) -> np.ndarray:
    tau = ratio_critico(costos["quiebre_por_unidad"], costos["merma_por_unidad"])
    return cuantil_interpolado(P, niveles, tau).clip(min=0)
