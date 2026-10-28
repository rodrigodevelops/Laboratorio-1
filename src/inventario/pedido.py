def ratio_critico(costo_quiebre: float, costo_merma: float) -> float:
    """Cuantil de la demanda que minimiza el costo esperado (newsvendor)."""
    if costo_quiebre <= 0 or costo_merma <= 0:
        raise ValueError("Los costos deben ser positivos")
    return costo_quiebre / (costo_quiebre + costo_merma)