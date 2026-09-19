"""Codigo PORTADO desde la rama main, tal cual. No se refactoriza aqui.

  origen:  github.com/lszefner/hackspain, rama main, commit cb24cb2
  de:      rules_ingestion/{loader,catalog,classify,normalize,store,
                            merge,policy_metrics}.py
  portado: 2026-09-19

NO se trajeron:
  · codegen.py  -- genera codigo Python con un LLM y lo ejecuta. Riesgo alto
                   y no hace falta: las reglas nuevas se exportan como
                   condiciones declarativas, no como codigo.
  · checks.py, invoice.py -- son el motor de decision de main. Aqui ya hay
                   uno (alberto/reglas/motor.py) con 46 tests detras.

Si hay que corregir algo, se corrige AQUI y se anota en el CHANGELOG: este
directorio es la frontera con el codigo de otro equipo.
"""
__version__ = "portado-cb24cb2"
