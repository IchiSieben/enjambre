# Worktrees

## Qué es

Un *worktree* es una segunda (o tercera) carpeta de trabajo del mismo repositorio, con su propia rama activa. Todas comparten un único `.git`: no se copia el historial. `git worktree add <carpeta> -b <rama>` crea la carpeta y la rama de una vez.

## Por qué importa aquí

Cada tarea del enjambre se ejecuta en **su propio worktree y su propia rama** (`mac/<id>`). Dos trabajos, o un trabajo y tú, nunca escriben el mismo archivo, y el resultado es revisable y desechable: si la rama no sirve, se descarta. Con un solo checkout, un agente a medias dejaría tu árbol de trabajo sucio. Las decisiones están en `../tutorial/08-primera-tarea-distribuida.md` y la razón en `worker.sh`.

El worker además se niega a reutilizar un id: si el worktree ya existe, termina con error en vez de pisarlo.

## Ejemplo real

De `worker.sh`:

```
git worktree add -q "$wt" -b "$branch" HEAD
```

Y cuando ya existe:

```
{"task_id":"<id>","exit":2,"error":"worktree exists: refuse to reuse a task id"}
```

Resultado en el ledger: `branch: mac/changelog-stub`, con el commit y su `result.json`.

## Error típico

Intentar tener la misma rama en dos worktrees: git lo rechaza (una rama solo puede estar activa en un worktree). Otro: olvidar limpiar. Los worktrees viejos se acumulan; `git worktree list` los muestra y `git worktree remove` los quita. En este proyecto no se ha automatizado esa limpieza (no hay evidencia en las fuentes de que exista).

## Para saber más

- git-worktree: https://git-scm.com/docs/git-worktree
