# Publicar enjambre: checklist

Este repositorio público se genera desde el repo privado de trabajo con un exportador que no se
publica: lista de inclusión, tabla de sustituciones, exclusiones afirmadas y **un solo commit sin
la historia privada**. Cada casilla lleva el comando que la prueba y su salida.

## Antes del primer push

- [x] **Auditoría dura en verde, árbol e historial** (2026-10-11)
  ```
  $ python tools/publicar/auditar.py .
  auditoría: VERDE (0 hallazgos) en <copia exportada>
  ```
  Reglas: IPv4/IPv6 fuera de loopback y rangos de documentación, MAC (con `:` y `-`), números de
  serie y UUID de hardware, nombres de máquina (`DESKTOP-…`, `*.local`), rutas de usuario
  (`/Users/<nombre>`, `C:\Users\<nombre>`), correos, claves por forma, palabras prohibidas
  (HMAC con clave fuera del repo) y binarios no revisados. Sin clave sale con código 2: nunca
  hay verde con una regla apagada.
- [x] **Tests en verde** (2026-10-11)
  ```
  Windows 10, en la copia exportada:          405 passed, 2 skipped
  ```
  La copia exportada en POSIX la prueba el CI (Linux) en su primera ejecución.
- [x] **Identidad del commit**: autor y committer `IchiSieben <IchiSieben@users.noreply.github.com>`;
  la auditoría revisa también autores y parches de toda la historia.
- [x] **Nada de la lista negra**: `state/`, `jobs.jsonl`, `nodes.toml` real, `config/` con rutas
  propias, notas del dueño. El exportador falla si aparece cualquiera.
- [x] **Enlaces**: ningún enlace relativo de la documentación apunta a un archivo ausente
  (lo comprueba el exportador).
- [x] **Capturas solo del modo demo**: `tools/visor/capturar_demo.py`, grabadas en la Mac,
  revisadas a tamaño real; sus sha256 están en `tools/publicar/binarios.txt`. Las capturas del
  tutorial (entorno real) no se publican.
- [x] `README.md` (ES) y `README.en.md` (EN), `SECURITY.md`, `LICENSE` (MIT), `.gitignore`,
  `nodes.example.toml`, `docs/ESTUDIO.md`, `docs/FICHA-LANDING.md`, `docs/CAPTURAS.md`.
- [x] **Demo estática**: `python enjambre.py visor --demo --export` escribe `state/web-dist/demo/`
  (`index.html`, `live.js`, `ring.js`, `demo-fixture.json`, `.htaccess`) y la audita: VERDE.
  Probada servida por HTTP en Chromium: sin errores de página, sin respuestas 4xx, panel de detalle
  con curvas, sin scroll horizontal a 390 px.
- [x] **CI** (`.github/workflows/auditoria.yml`): la auditoría con `fetch-depth: 0` y la suite
  corren en cada push y en cada pull request. El secreto es obligatorio en push. Un pull request
  desde un fork no recibe secretos: la auditoría se salta con un aviso en el log y los tests corren
  igual. Primera ejecución, 2026-10-15, sobre el commit `c5434ac`:
  ```
  auditoría: VERDE (0 hallazgos) en enjambre
  402 passed, 5 skipped in 6.70s          # ubuntu-latest, Python 3.12
  ```
- [x] **Testigo de la clave** (2026-10-15): `prohibidas.txt` lleva una línea más, el HMAC de un
  testigo fijo. Si el secreto del CI estuviera mal copiado, no coincidiría ninguna palabra y la
  auditoría daría un verde falso; sin el testigo, no hay verde.
  ```
  $ AUDITORIA_CLAVE=otra python tools/publicar/auditar.py .
  auditoría: CLAVE DISTINTA: AUDITORIA_CLAVE no es la clave de prohibidas.txt (falta su testigo); no hay verde
  ```

## Primer push (2026-10-15)

```
$ git push -u origin main
 * [new branch]      main -> main
$ gh repo view IchiSieben/enjambre --json visibility,nameWithOwner,defaultBranchRef
{"defaultBranchRef":{"name":"main"},"nameWithOwner":"IchiSieben/enjambre","visibility":"PUBLIC"}
$ gh api repos/IchiSieben/enjambre/commits --jq '.[] | [.sha[0:7], .commit.author.email, .commit.committer.email] | @tsv'
c5434ac  IchiSieben@users.noreply.github.com  IchiSieben@users.noreply.github.com
```

Los cambios posteriores llegan como commits nuevos encima de ese primero, con la misma identidad
noreply y la auditoría de árbol e historial antes de cada push. La historia publicada nunca se
reescribe.

## Lo que decide el dueño

- [x] Nombre del repositorio público: `IchiSieben/enjambre` (2026-10-11).
- [x] Subdominio de la demo: `enjambre.ichisieben.dev`, estático (2026-10-11).
- [x] Secreto de Actions `AUDITORIA_CLAVE` en el repo nuevo (2026-10-15; sin él, el CI falla a propósito).
- [x] Ver esta auditoría en verde y dar el OK al primer push público (2026-10-15).
- [ ] `docs/arquitectura.*`: pendiente de la plantilla nueva de arquitectura viva del Landing.
