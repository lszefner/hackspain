"""Autoria de reglas: de un CSV o un Excel a una norma que el motor entiende.

Esto corre en tiempo de AUTORIA, nunca al decidir. Produce un
`norma_vN.yaml`; a partir de ahi el motor es el de siempre y la decision
sigue siendo una funcion pura de un fichero congelado. Si el clasificador
se cae, no hay ninguna decision que se vea afectada.
"""
from .exportador import exportar_norma, ingerir
__all__ = ["ingerir", "exportar_norma"]
