import pandas as pd
import pytest

from inventario.validacion import DatosInvalidos, validar

TIENDAS = {1, 2}
FAMILIAS = {"BEVERAGES", "GROCERY I"}


def ventas(**cambios):
    df = pd.DataFrame({
        "date": ["2017-01-05", "2017-01-05"],
        "store_nbr": [1, 2],
        "family": ["BEVERAGES", "GROCERY I"],
        "sales": [10.0, 0.0],
    })
    for col, valor in cambios.items():
        df[col] = valor
    return df


def test_archivo_valido_con_ceros_no_genera_advertencias():
    df, advertencias = validar(ventas(), "ventas", TIENDAS, FAMILIAS, pd.Timestamp("2017-01-05"))
    assert advertencias == []
    assert df["date"].dtype.kind == "M"


@pytest.mark.parametrize("cambio", [
    {"sales": [-1.0, 3.0]},
    {"sales": [None, 3.0]},
    {"store_nbr": [1, 99]},
    {"family": ["BEVERAGES", "DESCONOCIDA"]},
    {"date": ["2017-01-05", "no es fecha"]},
])
def test_valores_invalidos_detienen_el_archivo(cambio):
    with pytest.raises(DatosInvalidos):
        validar(ventas(**cambio), "ventas", TIENDAS, FAMILIAS)


def test_duplicados_son_invalidos():
    with pytest.raises(DatosInvalidos, match="duplicadas"):
        validar(ventas(store_nbr=[1, 1], family=["BEVERAGES"] * 2), "ventas", TIENDAS, FAMILIAS)


def test_columna_faltante_es_invalida():
    with pytest.raises(DatosInvalidos, match="faltan columnas"):
        validar(ventas().drop(columns="sales"), "ventas")


def test_fecha_distinta_a_la_del_archivo_es_invalida():
    with pytest.raises(DatosInvalidos, match="fecha distinta"):
        validar(ventas(), "ventas", TIENDAS, FAMILIAS, pd.Timestamp("2017-01-06"))


def test_petroleo_vacio_es_advertencia_no_error():
    df = pd.DataFrame({"date": ["2017-01-05"], "dcoilwtico": [None]})
    _, advertencias = validar(df, "petroleo")
    assert advertencias
