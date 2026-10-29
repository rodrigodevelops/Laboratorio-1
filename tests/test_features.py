"""La prueba más importante: ninguna feature puede usar información del futuro."""

import numpy as np
import pandas as pd

from inventario.config import Rutas, cargar_params, corte_historico
from inventario.features import construir_contexto, filas_corte, tabla_entrenamiento
from inventario.ingesta import ingestar

CORTE = pd.Timestamp("2017-01-30")


def _contexto(proyecto, fecha_vista=CORTE):
    params = cargar_params(proyecto / "config" / "params.yaml")
    rutas = Rutas.desde_params(params)
    ingestar(rutas, "2017-02-20", corte_historico(params), params)
    return construir_contexto(rutas.raw, fecha_vista, 14)


def test_la_vista_al_corte_no_tiene_ventas_del_corte_en_adelante(proyecto):
    ctx = _contexto(proyecto)
    assert np.isnan(ctx.ventas[:, ctx.pos(CORTE):]).all()


def test_features_no_cambian_si_cambia_el_futuro(proyecto):
    ctx = _contexto(proyecto, fecha_vista=pd.Timestamp("2017-02-20"))
    antes = filas_corte(ctx, CORTE, 14, con_objetivo=False)
    pc = ctx.pos(CORTE)
    ctx.ventas[:, pc:] = 1e9           # ventas desde el día del corte
    ctx.trans[:, pc - 2:] = 1e9        # transacciones que aún no habrían llegado
    ctx.petroleo[pc - 1:] = 1e9        # petróleo que aún no habría llegado
    despues = filas_corte(ctx, CORTE, 14, con_objetivo=False)
    pd.testing.assert_frame_equal(antes, despues)


def test_objetivos_de_entrenamiento_son_anteriores_al_corte(proyecto):
    ctx = _contexto(proyecto)
    tabla = tabla_entrenamiento(ctx, CORTE, 14, 4, 7)
    assert tabla["date"].max() < CORTE
    assert (tabla["date"] > tabla["corte"]).all()
    assert (tabla["date"] - tabla["corte"]).dt.days.between(1, 14).all()
