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

## 7. Pendientes

- Horizonte de pronóstico y frecuencia de corte.
- Modelos, esquema de validación temporal y criterio de reemplazo.
- Métrica de negocio a partir de los costos de `params.yaml`.
