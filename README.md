# Motor de Reposición de Inventario

Producto de ML que, cada lunes, sugiere cuánto pedir al centro de distribución
por tienda y familia de producto, a partir de los datos de ventas que llegan a
diario. Usa los datos de Corporación Favorita (Kaggle) y está construido con
**Git**, **DVC** y **Apache Airflow 3**, de modo que cada pedido publicado se puede
rastrear hasta el código, los datos y el modelo que lo produjeron.

En cada fecha de corte el producto entrega:

1. **Pronóstico diario** de los próximos 14 días por tienda y familia, con
   cuantiles 10 %, 25 %, 50 %, 75 % y 90 %.
2. **Pedido sugerido** para los próximos 7 días, a partir del cuantil que
   minimiza el costo de quiebre más el de merma, con los costos definidos en
   `config/params.yaml`.
3. **Seguimiento del desempeño** de los pronósticos ya publicados contra las
   ventas reales, a medida que se conocen.

Las decisiones técnicas y de producto están en [DECISIONES.md](DECISIONES.md).

---

## Requisitos

- Docker con Docker Compose v2.14 o superior. En Windows, Docker Desktop con el
  backend de WSL 2 y al menos 6 GB de memoria para WSL.
- Git.
- Opcional, para desarrollo y para la evidencia con DVC: Python 3.11 o superior y
  `pip install -r requirements.txt "dvc[http]"`. En Ubuntu o WSL también
  `sudo apt install libgomp1`.

## Puesta en marcha desde cero

### 1. Clonar el repositorio

```bash
git clone https://github.com/rodrigodevelops/Laboratorio-1.git
cd Laboratorio-1
```

### 2. Colocar los datos de Kaggle

Descargar los datos de
<https://www.kaggle.com/competitions/store-sales-time-series-forecasting/data>
y copiar los archivos CSV en **`data/kaggle/`**:

```
data/kaggle/train.csv
data/kaggle/stores.csv
data/kaggle/holidays_events.csv
data/kaggle/oil.csv
data/kaggle/transactions.csv
```

Si también están `test.csv` y `sample_submission.csv`, no pasa nada: se ignoran.

### 3. Construir la imagen

```bash
echo "AIRFLOW_UID=$(id -u)" > .env
docker compose build
```

El archivo `.env` hace que los archivos que escribe Airflow queden a nombre de tu
usuario. En macOS o Windows sin WSL se puede omitir.

### 4. Generar la carga histórica y la bandeja

La bandeja simula el sistema de origen de la empresa: un archivo por fuente y por
fecha de entrega. Se genera a partir de los CSV de Kaggle, dentro del contenedor,
sin instalar Python localmente:

```bash
docker compose run --rm airflow-cli python -m inventario.bandeja --config config/params.yaml
```

Con DVC instalado, el equivalente es `dvc repro`.

### 5. Levantar Airflow

```bash
docker compose up -d
```

Abrir <http://localhost:8080> con usuario **airflow** y contraseña **airflow**.

El DAG `motor_reposicion` arranca activo y procesa solo, en orden, todo el periodo
de la bandeja (del 2017-01-01 al 2017-08-15 con la configuración actual). No hace
falta activar ni ejecutar nada a mano. Con 16 núcleos tarda cerca de una hora.

Para detenerlo: `docker compose down`. Para borrar también la base de datos de
Airflow: `docker compose down --volumes`.

---

## Cómo funciona

### La bandeja (sistema de origen)

```
data/bandeja/<fuente>/entrega=AAAA-MM-DD/<fuente>_<fecha_del_dato>.csv
```

La fecha de la carpeta es el día en que el dato queda disponible. El flujo solo
lee entregas con fecha menor o igual a la fecha que está procesando, y nunca
modifica la bandeja.

| Fuente | Llega | Columnas |
|---|---|---|
| `ventas` | día siguiente | `date, store_nbr, family, sales` |
| `promociones` | 14 días antes | `date, store_nbr, family, onpromotion` |
| `transacciones` | 2 días después (a veces más) | `date, store_nbr, transactions` |
| `petroleo` | día siguiente, solo días hábiles | `date, dcoilwtico` |
| `feriados` | 1 de diciembre del año anterior | columnas de `holidays_events.csv` |

Una **corrección** es otro archivo con la misma fecha de dato y una fecha de
entrega posterior: reemplaza el día completo.

### El DAG `motor_reposicion`

Una ejecución por día (`ds` = cierre del día):

1. `ingesta_y_validacion`: copia a `data/raw` las entregas disponibles, valida
   cada archivo y manda a cuarentena los inválidos.
2. `es_dia_de_corte`: los lunes sigue al pronóstico; los demás días salta al
   seguimiento.
3. `pronostico` (lunes): evalúa los modelos cuando corresponde, prepara los datos
   y publica el pronóstico y el pedido.
4. `seguimiento`: compara todos los pronósticos publicados con las ventas reales
   conocidas.

Las tareas son idempotentes y toda la lógica está en `src/inventario`; el DAG
solo orquesta.

### Resultados

| Ruta | Contenido |
|---|---|
| `data/published/corte=AAAA-MM-DD/pedido.csv` | Pedido sugerido por tienda y familia |
| `data/published/corte=AAAA-MM-DD/pronostico.parquet` | Cuantiles diarios de todos los modelos |
| `data/published/corte=AAAA-MM-DD/manifest.json` | Commit, parámetros, datos usados y hash |
| `data/evaluacion/fecha=AAAA-MM-DD/comparacion.md` | Comparación de modelos (backtest) |
| `data/seguimiento/fecha=AAAA-MM-DD/reporte.md` | Desempeño de lo publicado contra lo real |
| `models/vigente/fecha=AAAA-MM-DD.json` | Decisiones del modelo vigente |
| `data/raw/_cuarentena/` | Archivos inválidos y sus errores |
| `data/raw/_ingestas/fecha=AAAA-MM-DD.json` | Resumen de cada ingesta |

### Ejecutar sobre otro periodo

1. Cambiar `bandeja.fecha_inicio` y `bandeja.fecha_fin` en `config/params.yaml`.
2. Borrar las salidas anteriores:
   `rm -rf data/raw data/processed data/published data/evaluacion data/seguimiento models`
3. Regenerar la bandeja (paso 4) y reiniciar Airflow: `docker compose down --volumes && docker compose up -d`.

También se pueden agregar archivos directamente en `data/bandeja/` con el mismo
formato: el flujo los toma en la ejecución de su fecha de entrega.

### Reprocesar días pasados

Las correcciones se incorporan solas. Para volver a ejecutar un periodo, en la
interfaz se seleccionan las ejecuciones y se usa **Clear**, o desde la terminal:

```bash
docker compose exec airflow-scheduler airflow backfill create \
  --dag-id motor_reposicion --from-date 2017-03-01 --to-date 2017-03-31 \
  --reprocess-behavior completed
```

Como las tareas son idempotentes, repetir un día produce el mismo resultado.

---

## Configuración

Todo está en [`config/params.yaml`](config/params.yaml):

- `costos`: costo por unidad de quedarse sin producto y de merma. Cambiarlos
  cambia el pedido sin modificar el código ni reentrenar.
- `producto`: día de corte, horizonte, días que cubre el pedido y cuantiles.
- `modelo` y `evaluacion`: entrenamiento de LightGBM, backtest y criterio de
  reemplazo del modelo vigente.
- `bandeja`: periodo, retrasos y correcciones de la simulación.

## Pruebas

```bash
pip install -r requirements.txt
pytest -q
```

Corren en cada Pull Request con GitHub Actions. Usan datos sintéticos, porque
los datos reales no están en Git.

## Uso sin Airflow

La misma lógica se puede ejecutar desde la línea de comandos:

```bash
export PYTHONPATH=src
python -m inventario.cli ejecutar-dia --fecha 2017-01-02
python -m inventario.cli backfill --desde 2017-01-01 --hasta 2017-01-31
python -m inventario.cli reproducir --corte 2017-01-09
```

---

## Versionado con DVC

Ningún dato ni modelo está en Git. DVC los versiona con dos remotos:

- `publico` (por defecto): espejo de solo lectura en un dataset público de Hugging Face. Permite `dvc pull` **sin credenciales**.
- `origin`: DagsHub, donde el autor sube los datos.

Archivos versionados:

| Archivo `.dvc` | Contenido |
|---|---|
| `data/kaggle.dvc` | Datos originales de Kaggle |
| `dvc.lock` (etapa `generar_bandeja`) | Carga histórica y bandeja |
| `data/raw.dvc` | Datos ingeridos y validados |
| `data/processed.dvc` | Tablas del modelo por corte |
| `data/published.dvc` | Pronósticos y pedidos publicados |
| `models.dvc` | Modelos entrenados y decisiones del vigente |
| `data/evaluacion.dvc`, `data/seguimiento.dvc` | Reportes |

Para descargar los datos versionados basta con:

```bash
pip install dvc
dvc pull
```

Se usó un espejo en Hugging Face porque DagsHub exige iniciar sesión para leer su almacenamiento, incluso en repositorios públicos (ver DECISIONES.md, sección 15).

Para subir datos nuevos (solo el autor): `dvc push -r origin` y luego sincronizar el espejo copiando `.dvc/cache/files` al dataset de Hugging Face.

### Evidencia: reproducción de un pronóstico pasado

Se reprodujo el pronóstico publicado del corte **2017-03-06** en un clon limpio
del repositorio, sin credenciales, a partir de la versión de código y de datos del tag
`entrega-lab1`.

```bash
git clone https://github.com/rodrigodevelops/Laboratorio-1.git /tmp/reproduccion
cd /tmp/reproduccion
git checkout entrega-lab1
dvc pull data/raw.dvc data/published.dvc
cat data/published/corte=2017-03-06/manifest.json | head -20
PYTHONPATH=src python -m inventario.cli reproducir --corte 2017-03-06
```

Resultado obtenido:

```
{
  "corte": "2017-03-06",
  "hash_publicado": "906dc58e4a43053c2231e8733689c03bdf649dbba0e12a68d3e217840af5b729",
  "hash_reproducido": "906dc58e4a43053c2231e8733689c03bdf649dbba0e12a68d3e217840af5b729",
  "identico": true,
  "pedido_identico": true
}
```

El hash del pronóstico recalculado coincide con el registrado en el
`manifest.json` del corte publicado. También se puede verificar desde Airflow con
el DAG manual `reproducir_pronostico`.

---

## Evidencia de funcionamiento

En [`docs/evidencia/`](docs/evidencia/):

- `airflow_*.png`: ejecuciones del DAG en la interfaz de Airflow.
- `comparacion_modelos.md`: comparación de baseline contra LightGBM con validación
  temporal.
- `seguimiento.md`: reporte final de seguimiento del desempeño.

## Estructura del repositorio

```
├── .github/workflows/ci.yml   # pytest en cada Pull Request
├── config/params.yaml         # costos, producto, modelo, bandeja
├── dags/                      # solo orquestación
├── src/inventario/            # toda la lógica, con pruebas
├── tests/
├── notebooks/                 # exploración de datos
├── docs/evidencia/
├── data/  models/             # versionados con DVC, no con Git
├── dvc.yaml  dvc.lock
├── Dockerfile  docker-compose.yaml
├── CLAUDE.md                  # reglas para el agente de programación
├── DECISIONES.md
└── README.md
```

## Uso de herramientas de IA

El desarrollo se apoyó en Claude como asistente de programación. Las reglas del
proyecto para el agente están en [CLAUDE.md](CLAUDE.md). Todo el código y las
decisiones fueron revisados, probados y justificados por el autor.
