"""Línea de comandos del producto. Los DAGs de Airflow llaman a estas mismas funciones.

Ejemplos:
    python -m inventario.cli ejecutar-dia --fecha 2017-01-02
    python -m inventario.cli backfill --desde 2017-01-01 --hasta 2017-03-31
    python -m inventario.cli reproducir --corte 2017-03-06
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

from inventario.config import Rutas, cargar_params, corte_historico, es_dia_de_corte
from inventario.evaluacion import evaluar, necesita_evaluacion
from inventario.ingesta import ingestar
from inventario.pronostico import preparar, pronosticar, reproducir
from inventario.seguimiento import seguimiento


def ejecutar_dia(params: dict, fecha) -> dict:
    """Todo lo que corre en un día: ingesta, y si es día de corte, pronóstico y pedido."""
    rutas = Rutas.desde_params(params)
    fecha = pd.Timestamp(fecha)
    resultado = {"ingesta": ingestar(rutas, fecha, corte_historico(params), params)}
    if es_dia_de_corte(fecha, params):
        if necesita_evaluacion(rutas, params, fecha):
            resultado["evaluacion"] = evaluar(rutas, params, fecha)
        preparar(rutas, params, fecha)
        resultado["pronostico"] = pronosticar(rutas, params, fecha)
    seguimiento(rutas, params, fecha)
    return resultado


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="inventario")
    parser.add_argument("--config", default="config/params.yaml")
    sub = parser.add_subparsers(dest="comando", required=True)
    for nombre in ("ejecutar-dia", "ingestar", "evaluar", "seguimiento"):
        sub.add_parser(nombre).add_argument("--fecha", required=True)
    for nombre in ("preparar", "pronosticar", "reproducir"):
        sub.add_parser(nombre).add_argument("--corte", required=True)
    b = sub.add_parser("backfill")
    b.add_argument("--desde", required=True)
    b.add_argument("--hasta", required=True)
    args = parser.parse_args(argv)

    params = cargar_params(args.config)
    rutas = Rutas.desde_params(params)
    if args.comando == "ejecutar-dia":
        r = ejecutar_dia(params, args.fecha)
        print(json.dumps({k: v for k, v in r.items() if k != "ingesta"}, indent=2, default=str))
    elif args.comando == "backfill":
        for fecha in pd.date_range(args.desde, args.hasta):
            ejecutar_dia(params, fecha)
            print(f"{fecha.date()} listo")
    elif args.comando == "ingestar":
        r = ingestar(rutas, args.fecha, corte_historico(params), params)
        print(json.dumps({k: (v if k != "nuevos" else len(v)) for k, v in r.items()}, indent=2))
    elif args.comando == "evaluar":
        print(json.dumps(evaluar(rutas, params, args.fecha), indent=2))
    elif args.comando == "seguimiento":
        print(seguimiento(rutas, params, args.fecha).to_string())
    elif args.comando == "preparar":
        print(preparar(rutas, params, args.corte))
    elif args.comando == "pronosticar":
        print(json.dumps(pronosticar(rutas, params, args.corte), indent=2, default=str))
    elif args.comando == "reproducir":
        r = reproducir(rutas, params, args.corte)
        print(json.dumps(r, indent=2))
        if not r["identico"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
