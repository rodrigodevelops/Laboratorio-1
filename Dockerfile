# Imagen de Airflow con las dependencias del producto.
FROM apache/airflow:3.2.2

USER root
# libgomp1: LightGBM la necesita para entrenar en paralelo.
# git: para registrar el commit en el manifiesto de cada pronóstico.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER airflow
COPY requirements-airflow.txt /tmp/requirements-airflow.txt
# Fijar apache-airflow evita que pip lo actualice o lo rompa al instalar dependencias.
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /tmp/requirements-airflow.txt
