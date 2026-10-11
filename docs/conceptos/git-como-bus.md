# Git como bus

## Qué es

Un *bus* es el canal por donde viajan los resultados entre piezas de un sistema. Aquí el canal es git: cada trabajo termina en una **rama** y el resultado se mueve con `push` y `fetch`. La alternativa tentadora es una carpeta compartida (SMB, Drive) donde todos leen y escriben.

## Por qué importa aquí

Con una carpeta compartida, dos procesos pueden escribir el mismo archivo a la vez. Es una carrera que no ves hasta que un archivo queda a medias. Con git, el resultado llega completo (un commit) o no llega, y cada trabajo tiene su rama. Es el primer principio de `PLAN.md` §4.1. Lo aplica `../tutorial/08-primera-tarea-distribuida.md`.

Cuando la Mac no pudo pushear (su token de GitHub está en el llavero, inaccesible por SSH), el bus siguió funcionando: Windows trajo la rama desde la Mac con `git fetch` por SSH, la escaneó con gitleaks y la pushó. Cambió quién hace el último salto, no el modelo. Lo mismo con `git bundle` para clonar sin credenciales (`docs/DECISIONES.md`, puntos 2, 9 y 14).

La carpeta compartida queda para binarios grandes (renders, datasets), no para coordinar trabajo.

## Ejemplo real

```
PS> python enjambre.py status
2026-10-04T11:54:02+00:00  changelog-stub    mac  stub-ok   exit=0 pushed=True mac/changelog-stub
2026-10-04T11:54:15+00:00  must-fail         mac  stub-fail exit=1 pushed=True mac/must-fail
```

Y la forma del traslado desde la Mac:

```
git fetch mac:Documents/Portfolio/<repo> mac/<id>:mac/<id>
```

## Error típico

Usar git pero con una sola rama para todos los trabajos: vuelves a tener la carrera, solo que en el `merge`. Otro: que el agente fusione a `main`. En este proyecto fusionar es decisión humana.

## Para saber más

- git-fetch: https://git-scm.com/docs/git-fetch
- git-bundle: https://git-scm.com/docs/git-bundle
