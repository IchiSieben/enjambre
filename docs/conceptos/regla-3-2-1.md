# Regla 3-2-1

## Qué es

Tres copias de los datos, en dos tipos de medio distintos, y una fuera de sitio. Una copia que nunca se verificó es una esperanza: por eso se compara con una suma de control (`sha256`) contra el original.

## Por qué importa aquí

Estado honesto, verificado en `docs/backup-audit-2026-10-04.md`:

- **Repos con remoto (26 privados ya pusheados):** disco de Windows, clon en la Mac, y GitHub (fuera de sitio). Son tres copias, pero las dos primeras son discos internos: el "dos medios" no se cumple del todo. Los clones de la Mac se verificaron por commit: cada uno quedó en el mismo commit que `origin/<rama>`.
- **Carpetas sin git:** una sola copia. `CARPETA-ML` (5023 MB), `carpeta-x` (3833 MB), `Despliegue-ml` (1798 MB) y otras. Es el hueco real. Propuesta de la auditoría: Google Drive (`Gdrive:05-Backups/`) para datos y modelos.
- **Respaldo de las fotos AirDrop de la Mac a un disco externo** (`prompts/06-mac-photos-backup.md`): **todavía no se ejecutó**. No hay `MANIFEST.csv` ni verificación `sha256` hecha, y no hay tarea de tutorial escrita sobre eso. Lo descrito aquí es el plan, no un resultado.

## Ejemplo real

Lo que pide el prompt 06 (instrucción, no salida):

```
rsync -a --checksum ...
shasum -a 256 <archivo>   # cada archivo copiado contra su origen
```

Más un `MANIFEST.csv` con ruta origen, ruta destino, tamaño y sha256, y la regla de **copiar, nunca mover**: borrar los originales es decisión posterior del owner.

## Error típico

Contar GitHub como respaldo de lo que no está en git, o de lo que git no tiene: archivos sin commitear (18 repos tenían cambios así) y repos *shallow*, cuya historia completa solo existe en GitHub. Otro: dos copias en el mismo disco físico cuentan como una.

## Para saber más

- rsync, documentación: https://rsync.samba.org/documentation.html
- SHA-256 (FIPS 180-4): https://csrc.nist.gov/pubs/fips/180-4/upd1/final
