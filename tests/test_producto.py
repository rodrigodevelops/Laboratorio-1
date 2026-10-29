"""Prueba de punta a punta del producto con datos sintéticos."""

import json

import pandas as pd

from inventario.cli import ejecutar_dia
from inventario.config import Rutas, cargar_params
from inventario.evaluacion import seleccionar
from inventario.pronostico import reproducir


def test_flujo_completo_reproducible_e_idempotente(proyecto):
    params = cargar_params(proyecto / "config" / "params.yaml")
    rutas = Rutas.desde_params(params)
    for fecha in pd.date_range("2017-01-01", "2017-01-25"):
        ejecutar_dia(params, fecha)

    cortes = sorted(p.name for p in rutas.published.glob("corte=*"))
    assert cortes == ["corte=2017-01-02", "corte=2017-01-09", "corte=2017-01-16", "corte=2017-01-23"]
    manifiesto = json.loads((rutas.published / "corte=2017-01-09" / "manifest.json").read_text())
    pedido = pd.read_csv(rutas.published / "corte=2017-01-09" / "pedido.csv")
    assert (pedido["pedido_sugerido"] >= 0).all()
    assert manifiesto["modelo_vigente"] in ("baseline", "lgbm")

    # Reproducir un corte pasado después de haber ingerido datos posteriores.
    assert reproducir(rutas, params, "2017-01-09")["identico"]

    # Volver a ejecutar un día de corte produce exactamente lo mismo.
    ejecutar_dia(params, "2017-01-09")
    otra = json.loads((rutas.published / "corte=2017-01-09" / "manifest.json").read_text())
    assert otra["hash_pronostico"] == manifiesto["hash_pronostico"]

    reporte = pd.read_csv(rutas.seguimiento / "fecha=2017-01-25" / "reporte.csv")
    assert set(reporte["modelo"]) == {"baseline", "lgbm"}
    assert (rutas.evaluacion / "fecha=2017-01-02" / "comparacion.md").exists()


def test_criterio_de_reemplazo():
    e = {"mejora_minima_costo": 0.02, "tolerancia_wape": 0.05}
    tabla = pd.DataFrame({"costo": [100.0, 99.0], "wape": [0.2, 0.2]}, index=["baseline", "lgbm"])
    assert seleccionar(tabla, "baseline", e)[0] == "baseline"   # mejora de 1 %: no alcanza
    tabla.loc["lgbm", "costo"] = 90.0
    assert seleccionar(tabla, "baseline", e)[0] == "lgbm"
    tabla.loc["lgbm", "wape"] = 0.3
    assert seleccionar(tabla, "baseline", e)[0] == "baseline"   # empeora demasiado el WAPE
    assert seleccionar(tabla, None, e)[0] == "lgbm"
