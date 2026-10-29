"""Flujo diario del motor de reposición.

Cada ejecución representa el cierre de un día ``ds``: procesa las entregas de la
bandeja con fecha menor o igual a ``ds``. Los lunes además pronostica, calcula el
pedido sugerido y lo publica. El seguimiento del desempeño corre todos los días.

El DAG solo orquesta: toda la lógica está en ``src/inventario`` y tiene pruebas.

- ``catchup=True`` y ``max_active_runs=1``: al activarse procesa todo el periodo
  de la bandeja en orden, sin intervención manual. Es el backfill.
- Todas las tareas son idempotentes: repetir un día produce el mismo resultado.
- Los archivos inválidos quedan en cuarentena y no detienen el flujo; las
  correcciones se incorporan solas porque cada corte reconstruye la vista de
  datos disponible a su fecha.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pendulum
import yaml
from airflow.sdk import dag, task, task_group

CONFIG = os.environ.get("INVENTARIO_CONFIG", "/opt/airflow/proyecto/config/params.yaml")

with open(CONFIG, encoding="utf-8") as f:
    _bandeja = yaml.safe_load(f)["bandeja"]
INICIO = pendulum.parse(_bandeja["fecha_inicio"], tz="UTC")
# La venta del último día llega al día siguiente.
FIN = pendulum.parse(_bandeja["fecha_fin"], tz="UTC").add(days=1)


def _contexto():
    from inventario.config import Rutas, cargar_params

    params = cargar_params(CONFIG)
    return params, Rutas.desde_params(params)


@dag(
    dag_id="motor_reposicion",
    schedule="@daily",
    start_date=INICIO,
    end_date=FIN,
    catchup=True,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=1)},
    tags=["inventario"],
    doc_md=__doc__,
)
def motor_reposicion():
    @task_group(group_id="ingesta_y_validacion")
    def ingesta_y_validacion():
        @task
        def ingestar_bandeja(ds=None) -> dict:
            from inventario.config import corte_historico
            from inventario.ingesta import ingestar

            params, rutas = _contexto()
            r = ingestar(rutas, ds, corte_historico(params), params)
            for advertencia in r["advertencias"]:
                print(f"ADVERTENCIA: {advertencia}")
            for c in r["cuarentena"]:
                print(f"CUARENTENA: {c['archivo']}: {c['errores']}")
            for c in r["corregidos"]:
                print(f"CORRECCIÓN: {c['fuente']} del {c['fecha']}")
            return {k: len(v) for k, v in r.items() if isinstance(v, list)}

        return ingestar_bandeja()

    @task.branch
    def es_dia_de_corte(ds=None) -> str:
        from inventario.config import es_dia_de_corte as es_corte

        params, _ = _contexto()
        return "pronostico.evaluar_modelos" if es_corte(ds, params) else "seguimiento.seguimiento_desempeno"

    @task_group(group_id="pronostico")
    def pronostico():
        @task
        def evaluar_modelos(ds=None) -> str:
            from inventario.evaluacion import evaluar, necesita_evaluacion

            params, rutas = _contexto()
            if not necesita_evaluacion(rutas, params, ds):
                return "No corresponde evaluar esta semana"
            decision = evaluar(rutas, params, ds)
            print(f"Modelo vigente: {decision['modelo']}. {decision['motivo']}")
            return decision["modelo"]

        @task
        def preparar_datos(ds=None) -> str:
            from inventario.pronostico import preparar

            params, rutas = _contexto()
            return str(preparar(rutas, params, ds))

        @task
        def pronosticar_y_pedir(ds=None) -> dict:
            from inventario.pronostico import pronosticar

            params, rutas = _contexto()
            m = pronosticar(rutas, params, ds)
            return {"corte": m["corte"], "modelo": m["modelo_vigente"], "hash": m["hash_pronostico"]}

        evaluar_modelos() >> preparar_datos() >> pronosticar_y_pedir()

    @task_group(group_id="seguimiento")
    def seguimiento():
        @task(trigger_rule="none_failed_min_one_success")
        def seguimiento_desempeno(ds=None) -> int:
            from inventario.seguimiento import seguimiento as calcular

            params, rutas = _contexto()
            return len(calcular(rutas, params, ds))

        return seguimiento_desempeno()

    ingesta = ingesta_y_validacion()
    rama = es_dia_de_corte()
    grupo_pronostico = pronostico()
    grupo_seguimiento = seguimiento()
    ingesta >> rama >> [grupo_pronostico, grupo_seguimiento]
    grupo_pronostico >> grupo_seguimiento


motor_reposicion()
