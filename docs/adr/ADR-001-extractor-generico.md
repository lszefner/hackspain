# ADR-001 · Un extractor generico en vez de 18 parsers por plantilla

**Contexto.** La Caja trae 500 facturas en 18 maquetas distintas. El plan inicial
asignaba a P2 escribir un parser por plantilla y un router que las distinguiera.
El ejemplo que nos pasaron intentaba resolverlo con regex sueltas sobre el texto
completo y acertaba el pedido en 219 de 471 (46%).

**Alternativas.**
1. 18 parsers, uno por maqueta. Cobertura total pero ~18 unidades de trabajo,
   y una maqueta nueva el sabado es un parser nuevo.
2. Un extractor guiado por FORMA (cuando el campo la tiene) y por ETIQUETA
   anclada a linea (cuando no), con tabla de sinonimos.
3. Mandarlo todo a un LLM.

**Decision.** La opcion 2. Los campos con forma canonica no necesitan saber la
maqueta: el pedido es siempre `PO-AAAA-NNNN`, el IBAN `ES` + 22, el NIF letra
mas 8 digitos. Solo los importes y la fecha necesitan etiqueta, y ahi basta una
tabla de sinonimos ordenada de mas especifica a menos.

**Consecuencias aceptadas.** Un campo sin forma ni etiqueta conocida no se
extrae; se marca ausente y la politica lo escala. Preferimos ese fallo, que es
visible y honesto, a un parser que adivina.

**Evidencia.** 471 facturas con capa de texto: pedido correcto en 468 (99,4%),
y los 3 restantes son anomalias reales (pedidos que no constan en el ERP).
Aritmetica interna cuadra en 462. Cero facturas con campos incompletos por
fallo del extractor. 2,7 s para las 500. Coste: 0 EUR.

**Dos trampas que solo aparecieron al medir:**
- El corpus mezcla `1.409,40` (espanol) y `EUR 1409.40` (punto decimal) en
  maquetas distintas. Se desambigua por la longitud de la cola tras el ultimo
  separador, no por localizacion supuesta.
- Hay TRES formatos de fecha: `26/01/2026`, `2026-01-26` y
  `15 de enero de 2026`. El tercero afectaba a 93 facturas.
