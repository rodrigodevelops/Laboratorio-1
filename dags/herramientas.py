"""DAGs manuales para evidencia y diagnóstico. No tienen calendario.

- ``reproducir_pronostico``: recalcula un corte publicado y falla si el hash no
  coincide con el del manifiesto.
- ``comparar_modelos``: corre el backtest y la regla de reemplazo para una fecha.
"""

from __future__ import annotations

import json
import os

import pendulum
from airflow.sdk import Param, dag, task

CONFIG = os.environ.get("INVENTARIO_CONFIG", "/opt/airflow/proyecto/config/params.yaml")


def _contexto():
    from inventario.config import Rutas, cargar_params

    params = cargar_params(CONFIG)
    return params, Rutas.desde_params(params)


@dag(
    dag_id="reproducir_pronostico",
    schedule=None,
    start_date=pendulum.datetime(2017, 1, 1, tz="UTC"),
    catchup=False,
    params={"corte": Param("2017-03-06", type="string", description="Lunes ya publicado (AAAA-MM-DD)")},
    tags=["inventario", "evidencia"],
)
def reproducir_pronostico():
    @task
    def reproducir(params=None) -> dict:
        from inventario.pronostico import reproducir as recalcular

        config, rutas = _contexto()
        resultado = recalcular(rutas, config, params["corte"])
        print(json.dumps(resultado, indent=2))
        if not resultado["identico"]:
            raise ValueError("El pronóstico reproducido no coincide con el publicado")
        return resultado

    reproducir()


@dag(
    dag_id="comparar_modelos",
    schedule=None,
    start_date=pendulum.datetime(2017, 1, 1, tz="UTC"),
    catchup=False,
    params={"fecha": Param("2017-08-14", type="string", description="Fecha de evaluación (AAAA-MM-DD)")},
    tags=["inventario", "evidencia"],
)
def comparar_modelos():
    @task
    def comparar(params=None) -> dict:
        from inventario.evaluacion import backtest, agregar

        config, rutas = _contexto()
        tabla = agregar(backtest(rutas, config, params["fecha"]))
        print(tabla.to_string())
        return tabla.round(6).to_dict(orient="index")

    comparar()


reproducir_pronostico()
comparar_modelos()
