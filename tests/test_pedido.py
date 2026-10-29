import numpy as np
import pytest

from inventario.metricas import costo_negocio, wape
from inventario.pedido import cuantil_interpolado, pedido_diario, ratio_critico

NIVELES = [0.1, 0.25, 0.5, 0.75, 0.9]
P = np.array([[1.0, 2.0, 4.0, 6.0, 8.0]])


def test_ratio_critico():
    assert ratio_critico(3.0, 1.0) == 0.75


def test_costos_iguales_dan_mediana():
    assert ratio_critico(1.0, 1.0) == 0.5


def test_costos_invalidos():
    with pytest.raises(ValueError):
        ratio_critico(0, 1.0)


def test_cuantil_interpolado():
    assert cuantil_interpolado(P, NIVELES, 0.5)[0] == 4.0
    assert cuantil_interpolado(P, NIVELES, 0.6)[0] == pytest.approx(4.8)
    assert cuantil_interpolado(P, NIVELES, 0.99)[0] == 8.0  # fuera de rango: extremo


def test_cambiar_costos_cambia_el_pedido_sin_reentrenar():
    barato = pedido_diario(P, NIVELES, {"quiebre_por_unidad": 1.0, "merma_por_unidad": 1.0})
    caro = pedido_diario(P, NIVELES, {"quiebre_por_unidad": 9.0, "merma_por_unidad": 1.0})
    assert caro[0] > barato[0]


def test_metricas_con_ceros():
    real = np.array([0.0, 10.0])
    assert wape(real, np.array([0.0, 8.0])) == pytest.approx(0.2)
    assert costo_negocio(real, np.array([1.0, 8.0]), {"quiebre_por_unidad": 3, "merma_por_unidad": 1}) == 7.0
