# La Caja · v1

Instantánea **literal** de lo que publicó la organización. No se edita nada de
aquí: si hay que cambiar algo, se cambia en nuestro código, no en la copia.

| | |
|---|---|
| Origen | `https://github.com/ikurotime/500-sombras-de-alberto.git` |
| Commit | `76ad52a63c1d263440dba78a2ce5fa19d686b50d` |
| Fecha del commit | viernes 18/09/2026 21:48:26 +0200 |
| Asunto | `update readme` |
| Capturado | sábado 19/09/2026 |
| Tamaño | 7,4 MB · 505 ficheros |

## Qué hay

| Ruta | Qué es |
|---|---|
| `facturas/` | Las 500 facturas del lote 1. 471 con capa de texto, 29 escaneadas |
| `FINAL_v7_DEFINITIVO_ahorasi.xlsx` | Proveedores, `Pedidos_2026` (516) y la hoja `Norma_Pagos_v3` |
| `MANUAL_ERP_2009.md` | El manual del bridge: login, paginación, `ORA-00600` |
| `alberto_erp.py` | El bridge ERP de 2009. `python3 alberto_erp.py` en el 8009 |
| `Makefile` | El de la organización, con los targets `erp*`. **No** es el nuestro |
| `README.md` | El de la organización. **No** es el nuestro |

`Makefile` y `README.md` existen también en la raíz del repo y **son distintos**:
los de la raíz los hemos ampliado nosotros. Los de aquí son los originales.
Los otros tres ficheros y las 500 facturas sí son idénticos byte a byte a los
de la raíz, comprobado al capturar.

## Comprobar que no se ha tocado

```bash
cd caja_de_alberto/v1 && shasum -a 256 -c MANIFIESTO.sha256 | grep -v ': OK$'
```

Sin salida es que está intacta.
