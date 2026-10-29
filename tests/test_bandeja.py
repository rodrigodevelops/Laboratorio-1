"""Pruebas de la generación de la bandeja.

Usan datos sintéticos pequeños con la misma estructura que Kaggle, porque los
datos reales no están en Git y el CI no tiene acceso a ellos.
"""

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from inventario.bandeja import ConfigBandeja, cargar_kaggle, generar, planificar_entregas

@pytest.fixture
def cfg() -> ConfigBandeja:
    return ConfigBandeja(fecha_inicio="2017-01-01", fecha_fin="2017-01-31", semilla=7)


def _contenido(directorio: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directorio).as_posix(): p.read_bytes()
        for p in sorted(directorio.rglob("*.csv"))
    }


def _fecha_entrega(ruta: Path) -> pd.Timestamp:
    return pd.Timestamp(ruta.parent.name.removeprefix("entrega="))


def test_retrasos_por_fuente(dir_kaggle, cfg):
    plan = planificar_entregas(cargar_kaggle(dir_kaggle), cfg)
    originales = plan[plan["version"] == 1]
    atraso = (originales["entrega"] - originales["clave"]).dt.days

    def de(fuente):
        return atraso[originales["fuente"] == fuente]

    assert (de("ventas") == cfg.retraso_ventas).all()
    assert (de("petroleo") == cfg.retraso_petroleo).all()
    assert (de("promociones") == -cfg.anticipacion_promociones).all()
    minimo = cfg.retraso_transacciones
    assert de("transacciones").between(minimo, minimo + cfg.retraso_extra_max).all()


def test_ninguna_fuente_pasa_de_fecha_fin(dir_kaggle, cfg):
    plan = planificar_entregas(cargar_kaggle(dir_kaggle), cfg)
    diarias = plan[plan["fuente"] != "feriados"]
    assert (diarias["clave"] <= cfg.fecha_fin).all()


def test_historico_y_bandeja_respetan_el_corte(tmp_path, dir_kaggle, cfg):
    generar(dir_kaggle, tmp_path / "hist", tmp_path / "bandeja", cfg)

    entregas = [_fecha_entrega(p) for p in (tmp_path / "bandeja").rglob("*.csv")]
    assert min(entregas) > cfg.corte_historico

    ventas = pd.read_csv(tmp_path / "hist" / "ventas.csv", parse_dates=["date"])
    llegada_ultima = ventas["date"].max() + pd.Timedelta(days=cfg.retraso_ventas)
    assert llegada_ultima <= cfg.corte_historico


def test_calendario_de_feriados_se_conoce_por_adelantado(tmp_path, dir_kaggle, cfg):
    generar(dir_kaggle, tmp_path / "hist", tmp_path / "bandeja", cfg)
    feriados = pd.read_csv(tmp_path / "hist" / "feriados.csv", parse_dates=["date"])
    # El calendario 2017 se publica el 2016-12-01, antes del corte histórico.
    assert (feriados["date"].dt.year == 2017).any()


def test_sin_archivo_de_ventas_el_25_de_diciembre(tmp_path, dir_kaggle, cfg):
    cfg = replace(cfg, fecha_inicio=pd.Timestamp("2016-12-20"))
    generar(dir_kaggle, tmp_path / "hist", tmp_path / "bandeja", cfg)
    nombres = {p.name for p in (tmp_path / "bandeja" / "ventas").rglob("*.csv")}
    assert "ventas_2016-12-24.csv" in nombres
    assert "ventas_2016-12-25.csv" not in nombres


def test_petroleo_conserva_los_nulos(tmp_path, dir_kaggle, cfg):
    generar(dir_kaggle, tmp_path / "hist", tmp_path / "bandeja", cfg)
    petroleo = pd.read_csv(tmp_path / "hist" / "petroleo.csv")
    assert petroleo["dcoilwtico"].isna().sum() == 1


def test_correcciones_reemplazan_el_dia_mas_tarde(tmp_path, dir_kaggle, cfg):
    cfg = replace(cfg, prob_correccion_ventas=1.0)
    generar(dir_kaggle, tmp_path / "hist", tmp_path / "bandeja", cfg)

    por_dia: dict[str, list[Path]] = {}
    for ruta in (tmp_path / "bandeja" / "ventas").rglob("*.csv"):
        por_dia.setdefault(ruta.name, []).append(ruta)
    assert por_dia

    llaves = ["date", "store_nbr", "family"]
    for rutas in por_dia.values():
        assert len(rutas) == 2
        original, corregido = sorted(rutas, key=lambda p: p.parent.name)
        dias = (_fecha_entrega(corregido) - _fecha_entrega(original)).days
        assert cfg.correccion_min_dias <= dias <= cfg.correccion_max_dias

        a, b = pd.read_csv(original), pd.read_csv(corregido)
        assert a[llaves].equals(b[llaves])
        assert (b["sales"] >= 0).all()
        assert (a["sales"] != b["sales"]).any()


def test_reproducible_e_idempotente(tmp_path, dir_kaggle, cfg):
    cfg = replace(cfg, prob_correccion_ventas=0.3, prob_retraso_extra_transacciones=0.3)
    generar(dir_kaggle, tmp_path / "h1", tmp_path / "b1", cfg)
    generar(dir_kaggle, tmp_path / "h2", tmp_path / "b2", cfg)
    primera = _contenido(tmp_path / "b1")
    assert primera == _contenido(tmp_path / "b2")
    assert _contenido(tmp_path / "h1") == _contenido(tmp_path / "h2")

    # Volver a generar sobre la misma carpeta no deja archivos sobrantes.
    (tmp_path / "b1" / "sobrante.csv").write_text("x\n")
    generar(dir_kaggle, tmp_path / "h1", tmp_path / "b1", cfg)
    assert _contenido(tmp_path / "b1") == primera


def test_no_modifica_los_datos_de_entrada(tmp_path, dir_kaggle, cfg):
    antes = _contenido(dir_kaggle)
    generar(dir_kaggle, tmp_path / "hist", tmp_path / "bandeja", cfg)
    assert _contenido(dir_kaggle) == antes


def test_rechaza_escribir_sobre_la_entrada(tmp_path, dir_kaggle, cfg):
    with pytest.raises(ValueError):
        generar(dir_kaggle, tmp_path / "hist", dir_kaggle, cfg)
    with pytest.raises(ValueError):
        generar(dir_kaggle, dir_kaggle / "hist", tmp_path / "bandeja", cfg)
