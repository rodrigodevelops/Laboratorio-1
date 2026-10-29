import hashlib
from pathlib import Path

import pandas as pd

from inventario.config import Rutas, cargar_params, corte_historico
from inventario.ingesta import ingestar, leer_vista


def _setup(proyecto: Path):
    params = cargar_params(proyecto / "config" / "params.yaml")
    return params, Rutas.desde_params(params), corte_historico(params)


def _huella(directorio: Path) -> dict:
    return {
        p.relative_to(directorio).as_posix(): hashlib.md5(p.read_bytes()).hexdigest()
        for p in sorted(directorio.rglob("*")) if p.is_file()
    }


def test_vista_solo_usa_lo_disponible(proyecto):
    params, rutas, corte = _setup(proyecto)
    ingestar(rutas, "2017-02-10", corte, params)
    vista = leer_vista(rutas.raw, "ventas", "2017-01-20")
    assert vista["available_at"].max() <= pd.Timestamp("2017-01-20")
    assert vista["date"].max() == pd.Timestamp("2017-01-19")


def test_no_modifica_la_bandeja(proyecto):
    params, rutas, corte = _setup(proyecto)
    antes = _huella(rutas.bandeja)
    ingestar(rutas, "2017-02-10", corte, params)
    assert _huella(rutas.bandeja) == antes


def test_idempotente(proyecto):
    params, rutas, corte = _setup(proyecto)
    ingestar(rutas, "2017-01-15", corte, params)
    primera = leer_vista(rutas.raw, "ventas", "2017-01-15")
    ingestar(rutas, "2017-01-15", corte, params)
    assert leer_vista(rutas.raw, "ventas", "2017-01-15").equals(primera)


def test_correccion_reemplaza_el_dia_solo_desde_su_entrega(proyecto):
    params, rutas, corte = _setup(proyecto)
    correcciones = {}
    for ruta in rutas.bandeja.glob("ventas/entrega=*/*.csv"):
        correcciones.setdefault(ruta.name, []).append(pd.Timestamp(ruta.parent.name[8:]))
    nombre, entregas = next((n, sorted(e)) for n, e in correcciones.items() if len(e) == 2)
    dia = pd.Timestamp(nombre.removeprefix("ventas_").removesuffix(".csv"))
    original, corregida = entregas

    ingestar(rutas, "2017-02-28", corte, params)
    antes = leer_vista(rutas.raw, "ventas", corregida - pd.Timedelta(days=1))
    despues = leer_vista(rutas.raw, "ventas", corregida)
    assert (antes.loc[antes["date"] == dia, "available_at"] == original).all()
    assert (despues.loc[despues["date"] == dia, "available_at"] == corregida).all()
    assert len(despues[despues["date"] == dia]) == len(antes[antes["date"] == dia])


def test_archivo_invalido_va_a_cuarentena_y_el_resto_sigue(proyecto):
    params, rutas, corte = _setup(proyecto)
    malo = rutas.bandeja / "ventas" / "entrega=2017-01-11" / "ventas_2017-01-10.csv"
    df = pd.read_csv(malo)
    df.loc[0, "sales"] = -5
    df.to_csv(malo, index=False)

    resumen = ingestar(rutas, "2017-01-12", corte, params)
    assert [c["archivo"] for c in resumen["cuarentena"]] == ["ventas/entrega=2017-01-11/ventas_2017-01-10.csv"]
    vista = leer_vista(rutas.raw, "ventas", "2017-01-12")
    assert pd.Timestamp("2017-01-10") not in set(vista["date"])
    assert pd.Timestamp("2017-01-11") in set(vista["date"])


def test_venta_que_no_llega_es_advertencia(proyecto):
    params, rutas, corte = _setup(proyecto)
    for ruta in (rutas.bandeja / "ventas" / "entrega=2017-01-11").glob("*.csv"):
        ruta.unlink()
    resumen = ingestar(rutas, "2017-01-11", corte, params)
    assert any("2017-01-10" in a for a in resumen["advertencias"])
