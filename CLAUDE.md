# Reglas del proyecto para el agente de programación

Motor de reposición de inventario: pronóstico por cuantiles y pedido sugerido
para Corporación Favorita, con Git, DVC y Airflow 3. Leer `README.md` y
`DECISIONES.md` antes de proponer cambios.

## Reglas no negociables

1. **Disponibilidad de la información.** Toda predicción usa solo datos con
   `available_at <= fecha de corte`. Ninguna feature puede mirar el futuro. Si se
   agrega una feature, agregar su caso a `tests/test_features.py`.
2. **La bandeja es de solo lectura.** Nada en `src/inventario` fuera de
   `bandeja.py` escribe en `data/bandeja/`. El producto la trata como un sistema
   externo.
3. **Los DAGs solo orquestan.** La lógica va en `src/inventario/` con pruebas.
   Un DAG solo llama funciones y registra resultados. Las importaciones pesadas
   van dentro de las tareas, no al inicio del archivo.
4. **Idempotencia.** Cada tarea escribe en su propia partición (`entrega=`,
   `corte=`, `fecha=`) y la sobrescribe completa. Ejecutar dos veces debe dar el
   mismo resultado.
5. **Nada de datos ni modelos en Git.** Todo lo que está bajo `data/` y `models/`
   se versiona con DVC. Nunca hacer commit de CSV, Parquet o modelos.
6. **Configuración, no código.** Costos, fechas, horizontes, cuantiles y
   parámetros de modelos van en `config/params.yaml`. Nada de valores de negocio
   en el código.
7. **Validación.** Los datos inválidos van a cuarentena y no contaminan el
   resultado; las situaciones esperadas del negocio (ventas en cero, días de
   cierre, datos tardíos, petróleo vacío) generan advertencias, nunca fallas.
8. **Reproducibilidad.** Semillas fijas, `deterministic=True` en LightGBM y
   manifiesto en cada publicación. Un cambio que rompa `reproducir` es un error.

## Forma de trabajo

- Ramas `feature/<tema>` y Pull Request a `main`; `main` está protegida.
- Todo cambio de lógica va con pruebas, y `pytest -q` debe pasar antes del PR.
- Las pruebas usan datos sintéticos (`tests/conftest.py`), nunca los de Kaggle.
- Cada decisión técnica o de producto nueva se documenta en `DECISIONES.md` en
  el mismo PR, con alternativas y justificación.
- Código y documentación en español.

## Comandos

```bash
source .venv/bin/activate
pytest -q
PYTHONPATH=src python -m inventario.cli ejecutar-dia --fecha 2017-01-02
docker compose up -d                 # Airflow en http://localhost:8080
docker compose logs airflow-scheduler --tail 50
```
