"""Centralita de proveedores: atiende por telefono lo que `alberto` decidio.

Es un CONSUMIDOR de la run, no parte de ella. Proceso aparte, puerto aparte,
la base de datos en solo lectura. Nada de aqui escribe en `alberto.db` ni
importa nada de `alberto` que no sea una consulta.
"""
