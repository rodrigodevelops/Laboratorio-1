"""Ingesta de la carga histórica y de la bandeja hacia ``data/raw``.

La bandeja se lee pero nunca se modifica. Cada archivo válido se copia a
``data/raw/<fuente>/entrega=AAAA-MM-DD/<archivo>.parquet`` con una columna
``available_at``. Los archivos inválidos quedan en cuarentena y no se usan.

``leer_vista`` reconstruye lo que se sabía de una fuente en una fecha dada: solo
usa entregas con ``available_at <= fecha`` y, para cada día, se queda con la
entrega más reciente (las correcciones reemplazan el día completo).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from inventario.config import Rutas
from inventario.validacion import DatosInvalidos, validar

FUENTES = ("ventas", "promociones", "transacciones", "petroleo", "feriados")
FUENTES_DIARIAS = ("ventas", "promociones", "transacciones", "petroleo")


def _fecha_carpeta(carpeta: Path) -> pd.Timestamp:
    return pd.Timestamp(carpeta.name.split("=", 1)[1])


def _escribir_parquet(df: pd.DataFrame, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".tmp")
    df.to_parquet(temporal, index=False)
    temporal.replace(ruta)


def _universo(dir_raw: Path) -> tuple[set[int], set[str]]:
    tiendas = pd.read_parquet(dir_raw / "tiendas.parquet")
    familias = pd.read_parquet(dir_raw / "familias.parquet")
    return set(tiendas["store_nbr"].astype(int)), set(familias["family"])


def ingestar_historico(rutas: Rutas, corte: pd.Timestamp) -> list[str]:
    """Carga inicial. Es idempotente: solo escribe lo que falta.

    Si la carga histórica es inválida el flujo no puede continuar, así que aquí
    sí se lanza la excepción.
    """
    advertencias = []
    destino_tiendas = rutas.raw / "tiendas.parquet"
    if not destino_tiendas.exists():
        tiendas = pd.read_csv(rutas.historico / "tiendas.csv")
        if tiendas["store_nbr"].duplicated().any():
            raise DatosInvalidos(["tiendas duplicadas en la carga histórica"])
        _escribir_parquet(tiendas, destino_tiendas)

    destino_familias = rutas.raw / "familias.parquet"
    if not destino_familias.exists():
        familias = pd.read_csv(rutas.historico / "ventas.csv", usecols=["family"])
        _escribir_parquet(
            pd.DataFrame({"family": sorted(familias["family"].unique())}), destino_familias
        )

    tiendas, familias = _universo(rutas.raw)
    for fuente in FUENTES:
        destino = rutas.raw / fuente / f"entrega={corte.date()}" / "historico.parquet"
        if destino.exists():
            continue
        df, adv = validar(pd.read_csv(rutas.historico / f"{fuente}.csv"), fuente, tiendas, familias)
        advertencias += [f"histórico {fuente}: {a}" for a in adv]
        _escribir_parquet(df.assign(available_at=corte), destino)
    return advertencias


def _archivos_bandeja(dir_bandeja: Path, fecha: pd.Timestamp):
    for fuente in FUENTES:
        for carpeta in sorted((dir_bandeja / fuente).glob("entrega=*")):
            entrega = _fecha_carpeta(carpeta)
            if entrega <= fecha:
                for ruta in sorted(carpeta.glob("*.csv")):
                    yield fuente, entrega, ruta


def _fecha_del_nombre(fuente: str, ruta: Path) -> pd.Timestamp | None:
    if fuente not in FUENTES_DIARIAS:
        return None
    return pd.Timestamp(ruta.stem.removeprefix(f"{fuente}_"))


def ingestar(rutas: Rutas, fecha, corte: pd.Timestamp, params: dict | None = None) -> dict:
    """Ingesta todas las entregas disponibles hasta ``fecha``.

    Idempotente: las entregas de ``fecha`` se reescriben siempre; las anteriores
    solo si faltan (por ejemplo, si una ejecución previa no ocurrió).
    """
    fecha = pd.Timestamp(fecha)
    resumen = {
        "fecha": str(fecha.date()),
        "nuevos": [],
        "corregidos": [],
        "cuarentena": [],
        "advertencias": ingestar_historico(rutas, corte),
    }
    tiendas, familias = _universo(rutas.raw)

    for fuente, entrega, origen in _archivos_bandeja(rutas.bandeja, fecha):
        relativo = Path(fuente) / f"entrega={entrega.date()}"
        destino = rutas.raw / relativo / f"{origen.stem}.parquet"
        marca = rutas.raw / "_cuarentena" / relativo / f"{origen.stem}.json"
        if entrega < fecha and (destino.exists() or marca.exists()):
            continue

        fecha_dato = _fecha_del_nombre(fuente, origen)
        try:
            try:
                crudo = pd.read_csv(origen)
            except Exception as e:  # archivo corrupto o ilegible
                raise DatosInvalidos([f"no se pudo leer: {e}"]) from e
            df, adv = validar(crudo, fuente, tiendas, familias, fecha_dato)
        except DatosInvalidos as e:
            marca.parent.mkdir(parents=True, exist_ok=True)
            marca.write_text(json.dumps({"archivo": str(origen), "errores": e.errores}, indent=2))
            destino.unlink(missing_ok=True)
            resumen["cuarentena"].append({"archivo": str(relativo / origen.name), "errores": e.errores})
            continue

        marca.unlink(missing_ok=True)
        if fecha_dato is not None and _existe_entrega_previa(rutas.raw / fuente, origen.stem, entrega):
            resumen["corregidos"].append({"fuente": fuente, "fecha": str(fecha_dato.date())})
        _escribir_parquet(df.assign(available_at=entrega), destino)
        resumen["nuevos"].append(str(relativo / origen.name))
        resumen["advertencias"] += [f"{origen.name}: {a}" for a in adv]

    resumen["advertencias"] += _revisar_llegadas(rutas.raw, fecha, params)
    registro = rutas.raw / "_ingestas" / f"fecha={fecha.date()}.json"
    registro.parent.mkdir(parents=True, exist_ok=True)
    registro.write_text(json.dumps(resumen, indent=2, ensure_ascii=False))
    return resumen


def _existe_entrega_previa(dir_fuente: Path, nombre: str, entrega: pd.Timestamp) -> bool:
    for ruta in dir_fuente.glob(f"entrega=*/{nombre}.parquet"):
        if _fecha_carpeta(ruta.parent) < entrega:
            return True
    return False


def _revisar_llegadas(dir_raw: Path, fecha: pd.Timestamp, params: dict | None) -> list[str]:
    """Avisa si no llegó la venta esperada. Es una advertencia, no un error."""
    retraso = (params or {}).get("validacion", {}).get("retraso_esperado_ventas", 1)
    esperada = fecha - pd.Timedelta(days=retraso)
    if esperada.month == 12 and esperada.day == 25:
        return []  # tiendas cerradas: situación esperada
    nombre = f"ventas_{esperada.date()}.parquet"
    disponibles = [
        p for p in (dir_raw / "ventas").glob(f"entrega=*/{nombre}") if _fecha_carpeta(p.parent) <= fecha
    ]
    if disponibles or _en_historico(dir_raw, esperada, fecha):
        return []
    return [f"no han llegado las ventas del {esperada.date()}"]


def _en_historico(dir_raw: Path, dia: pd.Timestamp, fecha: pd.Timestamp) -> bool:
    for ruta in (dir_raw / "ventas").glob("entrega=*/historico.parquet"):
        if _fecha_carpeta(ruta.parent) <= fecha:
            fechas = pd.read_parquet(ruta, columns=["date"])["date"]
            return bool((fechas == dia).any())
    return False


def leer_vista(dir_raw: Path, fuente: str, fecha) -> pd.DataFrame:
    """Lo que se sabía de ``fuente`` al final del día ``fecha``."""
    fecha = pd.Timestamp(fecha)
    partes = []
    for carpeta in sorted((Path(dir_raw) / fuente).glob("entrega=*")):
        if _fecha_carpeta(carpeta) <= fecha:
            partes += [pd.read_parquet(p) for p in sorted(carpeta.glob("*.parquet"))]
    if not partes:
        raise FileNotFoundError(f"No hay datos de {fuente} disponibles al {fecha.date()}")
    df = pd.concat(partes, ignore_index=True)
    grupo = df["date"].dt.year if fuente == "feriados" else df["date"]
    ultima = df.groupby(grupo)["available_at"].transform("max")
    return df[df["available_at"] == ultima].reset_index(drop=True)
