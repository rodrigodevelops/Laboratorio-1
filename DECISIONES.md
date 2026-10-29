# Decisiones técnicas y de producto

Este documento registra las decisiones tomadas en el proyecto, las alternativas
consideradas y su justificación. Se actualiza en el mismo Pull Request que
introduce cada decisión.

---

## 1. Hallazgos de la exploración de datos

Resultados del notebook `notebooks/01_eda.ipynb` que condicionan el diseño:

| Hallazgo | Consecuencia |
|---|---|
| `train.csv` va del 2013-01-01 al 2017-08-15 (3 000 888 filas) | Define el rango total disponible para histórico y bandeja |
| Solo falta el 25 de diciembre de cada año en las ventas | Es una situación esperada (tiendas cerradas), no un error |
| `oil.csv` llega hasta el 2017-08-31, después del fin de las ventas | Se aplica una fecha de fin común a todas las fuentes |
| `oil.csv` no tiene fines de semana y tiene 43 nulos en días hábiles | Son dos situaciones distintas: sin archivo vs. archivo con valor vacío |
| El 31 % de las ventas son cero | No se usa MAPE; los ceros no se tratan como errores |
| `transactions.csv` tiene unas 7 400 combinaciones tienda-día menos de las esperadas | Los faltantes son esperados; transacciones no es un insumo obligatorio |
| Los feriados van del 2012-03-02 al 2017-12-26 | El calendario cubre años completos y se conoce por adelantado |
| Hay 1 782 series (54 tiendas × 33 familias) | Universo de referencia para validar tiendas y familias |

---

## 2. Disponibilidad de la información en el tiempo

### 2.1 Fecha de entrega como `available_at`

**Decisión:** cada archivo de la bandeja se guarda en
`data/bandeja/<fuente>/entrega=AAAA-MM-DD/<fuente>_<fecha_dato>.csv`. La fecha de
la carpeta es el momento en que el dato queda disponible. El flujo solo lee
entregas con fecha menor o igual a su fecha de ejecución.

**Alternativas:**
- Un solo archivo por fuente con una columna `available_at`. Se descartó porque
  no se parece a un sistema de origen real, que entrega archivos sueltos.
- Nombrar el archivo solo con la fecha del dato. Se descartó porque no permite
  representar correcciones ni entregas tardías.

**Justificación:** el principio de usar solo información disponible queda
garantizado por la estructura de carpetas, y es fácil de probar.

### 2.2 Frecuencia y retraso de cada fuente

| Fuente | Frecuencia | Llega | Justificación |
|---|---|---|---|
| ventas | diaria | D+1 | El cierre del día se consolida en la noche |
| promociones | diaria | D−14 | Las promociones se planifican con anticipación |
| transacciones | diaria | D+2 (5 % de los días entre D+3 y D+5) | Viene de otro sistema y llega más lento |
| petróleo | días hábiles | D+1 | Precio de mercado; no hay dato en fines de semana |
| feriados | anual | 1 de diciembre del año anterior | Calendario oficial publicado por adelantado |
| tiendas | estática | carga histórica | Casi no cambia |

Todos los valores están en `config/params.yaml`, sección `bandeja`.

### 2.3 Separar promociones de ventas

**Decisión:** `onpromotion` se entrega como una fuente propia, con 14 días de
anticipación, aunque en `train.csv` viene junto con las ventas.

**Alternativa:** dejarla dentro del archivo de ventas. Se descartó porque solo se
conocería después de que ocurre el día, y no podría usarse para pronosticar.

**Justificación:** en la operación real las promociones se planifican antes. Así
pueden usarse como feature del horizonte de pronóstico sin violar la
disponibilidad de la información.

### 2.4 Regla de la carga histórica

**Decisión:** la carga histórica contiene todo lo que ya se había *entregado* al
cierre del día anterior a `fecha_inicio` (2016-12-31), no todo lo que *ocurrió*
antes de esa fecha.

**Consecuencias:**
- Las ventas del 2016-12-31 llegan por la bandeja el 2017-01-01.
- Las promociones del 1 al 14 de enero de 2017 ya están en el histórico.
- El calendario de feriados de 2017 está en el histórico, porque se publica el
  2016-12-01.

**Justificación:** aplica la misma regla de disponibilidad a la carga inicial y a
la bandeja, sin casos especiales.

### 2.5 Fecha de fin común

**Decisión:** ninguna fuente entrega datos con fecha posterior a `fecha_fin`
(2017-08-15).

**Justificación:** `oil.csv` llega hasta el 2017-08-31. Sin esta regla, la
bandeja contendría información de un periodo en el que no hay ventas.

---

## 3. Manejo de correcciones

**Decisión:** alrededor del 2 % de los días de ventas recibe una segunda entrega
entre 3 y 10 días después de la original. La corrección reemplaza el archivo del
día completo, y para cada fecha de dato gana la entrega más reciente disponible
al momento del corte.

**Alternativas:**
- Entregar solo las filas modificadas. Se descartó porque obliga a fusionar
  archivos parciales y complica la idempotencia.
- Marcar las correcciones con un sufijo en el nombre (`_v2`). Se descartó porque
  la fecha de entrega ya indica el orden.

**Justificación:** reemplazar el día completo hace que el reprocesamiento sea
simple y determinista.

---

## 4. Reproducibilidad de la bandeja

**Decisión:** la bandeja se genera con `src/inventario/bandeja.py` como una etapa
de DVC (`dvc.yaml`), con semilla fija. Cada proceso aleatorio (retrasos extra,
elección de días corregidos y valores corregidos) usa una semilla derivada
propia.

**Justificación:** con la misma configuración se obtienen exactamente los mismos
archivos. Cambiar un parámetro, como la probabilidad de corrección, no altera los
demás procesos aleatorios. El generador es idempotente: borra y recrea sus
directorios de salida.

**Separación de responsabilidades:** solo el generador escribe en la bandeja. El
producto la trata como un sistema externo de solo lectura.

---

## 5. Validación: situaciones esperadas vs. datos inválidos

| Situación | Tratamiento |
|---|---|
| Falta el archivo de ventas del 25 de diciembre | Esperado, no genera alerta |
| Falta el archivo de ventas de otro día | Alerta |
| Falta el archivo de petróleo en fin de semana | Esperado |
| Precio de petróleo nulo | Advertencia; se completa con el último valor conocido |
| Faltan transacciones de una tienda-día | Esperado |
| Ventas en cero | Esperado |
| Esquema inválido, ventas negativas, duplicados, tiendas o familias desconocidas | Error: se detiene el flujo |

Los casos inválidos no están en la bandeja principal: se prueban con datos
sintéticos en los tests.

### 5.1 Cuarentena

**Decisión:** un archivo inválido de la bandeja no se copia a `data/raw`: queda
registrado en `data/raw/_cuarentena/` con sus errores y el flujo continúa como si
ese archivo no hubiera llegado. Si llega una entrega posterior válida del mismo
día (una corrección), esa sí se usa.

**Alternativa:** fallar la ejecución completa. Se descartó porque un solo archivo
malo detendría todos los pronósticos siguientes, y la bandeja no se puede
modificar para arreglarlo.

**Excepción:** si la carga histórica es inválida, el flujo sí se detiene, porque
sin ella no hay producto.

---

## 6. Infraestructura

### 6.1 Remoto de DVC: DagsHub

**Alternativa considerada:** Google Drive. Se descartó porque la cuenta
institucional no permite crear proyectos en Google Cloud, y porque la
autenticación por navegador no funciona dentro de los contenedores de Airflow.

**Justificación:** DagsHub se autentica con usuario y token, se integra con
GitHub y no requiere configuración adicional.

### 6.2 Entorno de desarrollo: WSL2

**Justificación:** Smart App Control de Windows bloquea las librerías compiladas
de Python. Además, WSL2 es el backend de Docker Desktop y coincide con el
entorno Ubuntu del CI.

---

## 7. Definición del producto

| Parámetro | Valor | Justificación |
|---|---|---|
| Fecha de corte | lunes | Pedido semanal al centro de distribución |
| Horizonte | días corte+1 a corte+14 | Las ventas del día del corte aún no se conocen; 14 días cubren el pedido y la semana siguiente |
| Cobertura del pedido | días corte+1 a corte+7 | Un pedido por semana |
| Cuantiles | 0.10, 0.25, 0.50, 0.75, 0.90 | Expresan la incertidumbre y permiten interpolar cualquier cuantil intermedio |

**Pedido sugerido:** para cada día se toma el cuantil `Cu / (Cu + Co)` de la
demanda (modelo *newsvendor*); con los costos actuales es 0.75. El cuantil se
interpola entre los niveles pronosticados, así que cambiar los costos en
`config/params.yaml` cambia el pedido sin modificar el código ni reentrenar. El
pedido semanal es la suma de los pedidos diarios.

**Alternativa considerada:** calcular el cuantil de la demanda semanal total. Se
descartó por simplicidad: la suma de cuantiles diarios equivale a suponer
errores perfectamente correlacionados entre días, lo que da un pedido algo
conservador, coherente con que el quiebre cuesta más que la merma.

## 8. Información disponible en cada corte (features)

| Información | Hasta qué fecha se usa | Motivo |
|---|---|---|
| Ventas | corte−1 | Llegan con un día de retraso |
| Transacciones | corte−3 | Llegan con dos días de retraso y a veces más |
| Petróleo | corte−2, con el último valor conocido | Llega con un día de retraso; no hay fines de semana |
| Promociones | día objetivo | Se publican 14 días antes |
| Feriados | día objetivo | El calendario anual se publica por adelantado |

Features por serie: última venta, medias de 7, 28 y 91 días, proporción de ceros
en 28 días, las últimas 8 ventas del mismo día de la semana y su media reciente,
promoción del día objetivo y su media de 28 días, transacciones de la tienda,
petróleo, día de la semana, día del mes, mes, feriado, horizonte, tienda,
familia, tipo de tienda y clúster.

**Días de cierre:** el 25 de diciembre y el 1 de enero se tratan como días sin
dato. No entran en las features ni como objetivo de entrenamiento. En la primera
prueba con datos reales, el 1 de enero (ventas casi nulas en todas las tiendas)
hacía que LightGBM interpretara una caída general de la demanda y pronosticara
casi cero la semana siguiente (WAPE cercano a 1). La última venta observada se
toma como el último día con dato de la semana previa.

La prueba `tests/test_features.py` verifica que cambiar cualquier dato posterior
a lo disponible no cambia las features.

## 9. Modelos

- **Baseline:** cuantiles empíricos de las últimas 8 ventas del mismo día de la
  semana. No se entrena y captura la estacionalidad semanal.
- **LightGBM:** un modelo global para todas las series y un modelo por cuantil.
  Aprende la diferencia entre `log1p(ventas)` y el nivel reciente de cada serie
  (`log1p` de su media de 28 días), así un mismo patrón sirve para series grandes
  y pequeñas. Como los cuantiles no cambian al aplicar transformaciones
  monótonas ni al sumar una constante conocida, `expm1(predicción + nivel)` es el
  cuantil de las ventas.
- **Datos de entrenamiento:** los últimos 16 cortes semanales más 8 cortes
  alrededor de la misma semana del año anterior. Con solo las 16 semanas
  recientes, el modelo entrenado en diciembre no conocía la caída de enero y
  quedaba peor que el baseline en los primeros cortes de 2017 con datos reales.
  Los cortes del año anterior le muestran cómo cambia la demanda en esa época.
- **Reglas comunes:** los cuantiles se ordenan para que no se crucen, y las
  series sin ventas en las últimas 8 semanas reciben pronóstico 0.
- **Deep learning:** no se usa. Con 1 782 series y unos 4 años de historia, un
  modelo de árboles es competitivo, se entrena en un minuto sin GPU y es más
  fácil de reproducir y explicar. El beneficio esperado no justifica el costo de
  infraestructura y de tiempo de entrenamiento.

## 10. Validación y métricas

- **Esquema temporal:** origen móvil con 3 cortes semanales pasados, iguales para
  todos los modelos. En cada corte se entrena solo con datos anteriores y se
  mide contra las ventas reales de los 14 días siguientes.
- **Métricas técnicas:** WAPE de la mediana, pérdida pinball promedio y cobertura
  del intervalo 10 %–90 %. No se usa MAPE porque el 31 % de las ventas son cero.
- **Métrica de negocio:** costo total del pedido sugerido (unidades faltantes ×
  costo de quiebre + unidades sobrantes × costo de merma) en los 7 días de
  cobertura.
- **Limitación conocida:** el backtest usa la vista de datos vigente al momento
  de evaluar, que puede incluir correcciones posteriores a cada corte. Esto lo
  hace ligeramente optimista, pero afecta por igual a todos los modelos.

## 11. Criterio de reemplazo del modelo vigente

Cada 4 semanas (y en el primer corte) se evalúan todos los modelos. Un candidato
reemplaza al vigente solo si **reduce el costo de negocio al menos 2 %** y **no
empeora el WAPE más de 5 %**. Si no hay modelo vigente, se elige el de menor
costo.

Cada decisión se guarda con su fecha en `models/vigente/fecha=AAAA-MM-DD.json`.
Un corte usa la última decisión anterior o igual a su fecha, así que repetir un
corte pasado (backfill) usa el mismo modelo que se usó originalmente.

Todos los modelos se pronostican y publican en cada corte; el pedido sale del
vigente. Así el seguimiento compara los modelos con ventas reales en operación.

## 12. Idempotencia y reproducibilidad

- Cada tarea escribe en una partición propia (`entrega=`, `corte=`, `fecha=`) y la
  sobrescribe completa. Los pronósticos se escriben en una carpeta temporal y se
  renombran al final, para no dejar publicaciones a medias.
- Cada publicación incluye un `manifest.json` con el commit de Git, los
  parámetros usados, el inventario de datos disponibles y el hash del
  pronóstico.
- `python -m inventario.cli reproducir --corte AAAA-MM-DD` recalcula un corte
  publicado y compara el hash.
- LightGBM se entrena con semilla fija y `deterministic=True`.

## 13. Orquestación con Airflow

**Un DAG diario (`motor_reposicion`) con cuatro grupos de tareas:**
`ingesta_y_validacion` → `es_dia_de_corte` → `pronostico` (evaluar modelos,
preparar datos, pronosticar y pedir) → `seguimiento`. Los lunes se ejecuta el
grupo de pronóstico; los demás días se salta y el seguimiento corre igual.

**Alternativa considerada:** un DAG por flujo, conectados por *assets*. Se
descartó porque el pronóstico del lunes depende de la ingesta de ese mismo día, y
con DAGs separados el orden no está garantizado durante un backfill. En un solo
DAG la dependencia es explícita.

**Ejecución sin intervención manual:** el DAG se crea activo, con
`catchup=True`, `start_date` y `end_date` tomados del periodo de la bandeja en
`params.yaml`, y `max_active_runs=1` para procesar los días en orden. Al levantar
Airflow procesa todo el periodo solo.

**Semántica de fechas:** la ejecución con fecha `ds` representa el cierre del día
`ds` y procesa las entregas con fecha menor o igual a `ds`.

**Los DAGs solo orquestan:** cada tarea llama a una función de `src/inventario`,
que es la misma que usa la línea de comandos y que cubren las pruebas.

**DAGs manuales de evidencia:** `reproducir_pronostico` recalcula un corte
publicado y falla si el hash no coincide; `comparar_modelos` corre el backtest
para cualquier fecha.

**Imagen:** se extiende la imagen oficial `apache/airflow:3.2.2` con LightGBM,
`libgomp1` y `git` (para registrar el commit en el manifiesto). Se usa
`LocalExecutor` en lugar de Celery porque las tareas son secuenciales y no hace
falta un broker.

## 14. Pendientes

- Versionado con DVC de las salidas (raw, processed, publicados, modelos).
