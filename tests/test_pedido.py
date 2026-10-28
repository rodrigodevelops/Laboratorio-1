import pytest
from inventario.pedido import ratio_critico


def test_ratio_critico():
    assert ratio_critico(3.0, 1.0) == 0.75


def test_costos_iguales_dan_mediana():
    assert ratio_critico(1.0, 1.0) == 0.5


def test_costos_invalidos():
    with pytest.raises(ValueError):
        ratio_critico(0, 1.0)