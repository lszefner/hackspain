# La Caja · v2

Instantánea **literal** de lo que publicó la organización. No se edita nada de
aquí: si hay que cambiar algo, se cambia en nuestro código, no en la copia.

| | |
|---|---|
| Origen | `https://github.com/ikurotime/500-sombras-de-alberto.git` |
| Commit | `f831e3432d739cabcc3fb6d76d61eb58bec2ecd9` |
| Fecha del commit | 19/09/2026 17:57:37 +0200 |
| Asunto | `chore: preparaos que se vienen cositas` |
| Capturado | 19/09/2026 23:47:08 +0200 |
| Tamaño | 8.0M · 548 ficheros · 500 facturas |

## Comprobar que no se ha tocado

```bash
cd caja_de_alberto/v2 && shasum -a 256 -c MANIFIESTO.sha256 | grep -v ': OK$'
```

Sin salida es que está intacta.

## Qué cambió respecto a la anterior

```bash
diff caja_de_alberto/<anterior>/MANIFIESTO.sha256 caja_de_alberto/v2/MANIFIESTO.sha256
```
