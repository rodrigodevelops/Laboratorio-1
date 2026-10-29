"""Seguimiento del desempeño: pronósticos publicados contra ventas reales.

Se recalcula completo en cada ejecución con la vista de ventas más reciente, así
que si llega una corrección el reporte la incorpora sola.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from inventario.config import Rutas, niveles
from inventario.evaluacion import _tabla_md, metricas_modelo
from inventario.ingesta import leer_vista
from inventario.pronostico import columnas_cuantiles


def seguimiento(rutas: Rutas, params: dict, fecha) -> pd.DataFrame:
    fecha = pd.Timestamp(fecha)
    niv = niveles(params)
    qcols = columnas_cuantiles(niv)
    dias_pedido = params["producto"]["dias_cobertura_pedido"]
    ventas = leer_vista(rutas.raw, "ventas", fecha)[["date", "store_nbr", "family", "sales"]]

    filas = []
    for carpeta in sorted(Path(rutas.published).glob("corte=*")):
        corte = pd.Timestamp(carpeta.name.split("=", 1)[1])
        if corte > fecha or not (carpeta / "pronostico.parquet").exists():
            continue
        pronostico = pd.read_parquet(carpeta / "pronostico.parquet")
        cruce = pronostico.merge(ventas, on=["date", "store_nbr", "family"], how="inner")
        cruce = cruce[cruce["date"] <= fecha]
        for modelo, g in cruce.groupby("modelo"):
            en_cobertura = (g["h"] <= dias_pedido).to_numpy()
            metricas = metricas_modelo(
                g["sales"].to_numpy(), g[qcols].to_numpy(), niv, en_cobertura, params["costos"]
            )
            filas.append({"corte": corte, "modelo": modelo,
                          "dias_con_real": int(g["date"].nunique()), **metricas})

    columnas = ["corte", "modelo", "dias_con_real", "filas", "ventas", "error_abs",
                "wape", "pinball", "cobertura_80", "costo"]
    tabla = pd.DataFrame(filas, columns=columnas)
    destino = Path(rutas.seguimiento) / f"fecha={fecha.date()}"
    destino.mkdir(parents=True, exist_ok=True)
    tabla.to_csv(destino / "reporte.csv", index=False, date_format="%Y-%m-%d")

    texto = [f"# Seguimiento del desempeño al {fecha.date()}", ""]
    if tabla.empty:
        texto.append("Todavía no hay pronósticos publicados con ventas reales conocidas.")
    else:
        g = tabla.groupby("modelo")
        total = pd.DataFrame({
            "cortes": g.size(),
            "wape": g["error_abs"].sum() / g["ventas"].sum(),
            "cobertura_80": g["cobertura_80"].mean(),
            "costo": g["costo"].sum(),
        }).reset_index()
        detalle = tabla[["corte", "modelo", "dias_con_real", "wape", "cobertura_80", "costo"]].copy()
        detalle["corte"] = detalle["corte"].dt.strftime("%Y-%m-%d")
        texto += ["## Total", "", _tabla_md(total), "", "## Por corte", "", _tabla_md(detalle)]
    (destino / "reporte.md").write_text("\n".join(texto), encoding="utf-8")
    return tabla
