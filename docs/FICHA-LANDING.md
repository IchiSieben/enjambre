# Ficha para el Landing: enjambre

Esta página no edita el Landing. La sesión del Landing importa los dos bloques de abajo tal cual.

## Datos de la ficha

| Campo | Valor | Estado |
|---|---|---|
| Archivos destino | `Landing/src/content/projects/es/enjambre.md` y `Landing/src/content/projects/en/enjambre.md` | exacto |
| Segmento | `agentes-ia` | verificado en `SEGMENTS` de `config.ts` |
| Estado | `live` + `statusNote: "Open source · usable"` (el texto pedido va en `statusNote`, que el esquema sí tiene) | verificado en `config.ts` |
| Madurez | `usable` | verificado en el esquema |
| `repoUrl` | https://github.com/IchiSieben/enjambre | decidido por el owner (2026-10-11) |
| `demoUrl` | https://enjambre.ichisieben.dev | decidido por el owner (2026-10-11); demo estática (`visor --demo --export`), se sube después del primer push |
| Habilidades | `python`, `bash`, `git`, `llm-agentes`, `macos-arm`, `windows`, `sqlite` | los siete slugs existen en `Landing/src/content/skills/` |
| Portada | `/media/enjambre/poster.webp` (+ `poster-mobile.webp`, `preview.mp4`) | derivadas de `docs/img/visor-demo.png` y `visor-demo.mp4`; ver `CAPTURAS.md` |

Antes de importar: copiar los tres archivos a `Landing/public/media/enjambre/`. El esquema falla
el build si `media` apunta a un archivo que no existe. Si todavía no están, importar sin el bloque
`media`.

Si el subdominio no responde 200 el día de la importación, quitar `demoUrl` en vez de dejar un
enlace roto.

Cifras de `results`: 405 pruebas en verde (Windows, 2026-10-11, copia pública; el CI la repite en Linux) y 187 ms de latencia
`ssh` por Ethernet (mediana de 10, 2026-10-10, tutorial 29 §8).

## `Landing/src/content/projects/es/enjambre.md`

```markdown
---
title: "enjambre"
segment: "agentes-ia"
tech: ["ia-agentes", "automatizacion", "infra-servidores"]
sector: ["herramientas-dev"]
order: 12
featured: false
status: "live"
maturity: "usable"
statusNote: "Open source · usable"
published: true
since: 2026
date: "2026-10-11"
summary: "Programación con agentes repartida en hardware propio: una PC con Windows orquesta y una Mac mini ejecuta trabajos de Claude Code sin interfaz, cada uno en su worktree de git. Cada trabajo vuelve como una rama revisable y una línea en un registro de solo añadir, y un visor local muestra nodos, trabajos y telemetría."
oneLine: "Reparto trabajos de Claude Code entre mi PC y una Mac mini; cada uno vuelve como una rama que reviso, con un visor en vivo de las máquinas."
techLine: "Python · Bash · SSH · git worktrees · tmux · Claude Code"
repoUrl: "https://github.com/IchiSieben/enjambre"
demoUrl: "https://enjambre.ichisieben.dev"
skills:
  - "python"
  - "bash"
  - "git"
  - "llm-agentes"
  - "macos-arm"
  - "windows"
  - "sqlite"
applied:
  python: "Orquestador, enrutador, planificador y visor en biblioteca estándar, con una suite de 405 pruebas."
  bash: "worker.sh corre en el nodo, compatible con bash 3.2 de macOS, dentro de tmux para sobrevivir a una caída de SSH."
  git: "Git es el bus: un worktree y una rama por trabajo; los nodos nunca hacen push, la PC trae la rama, la escanea con gitleaks y la sube."
  llm-agentes: "Claude Code sin interfaz con lista explícita de herramientas, sin --dangerously-skip-permissions y con contrato de tarea (archivos permitidos y criterios de hecho)."
  macos-arm: "Mac mini M2 Pro de 16 GB como nodo de trabajo; el llavero se alcanza por SSH con un tmux nacido en la sesión gráfica."
  windows: "La PC con Windows es el orquestador; en Git Bash cada proceso lanzado cuesta unos 28 ms, y eso explica por qué la Mac gana por 14 a 27 veces en scripts bash."
  sqlite: "El visor guarda su propio historial de consumo, que no se borra."
problem: "Tengo una PC y una Mac mini que casi siempre están libres, y los agentes de código sin supervisión suelen correr con permisos de más o sin dejar rastro revisable. Quería repartir trabajo entre las dos con permisos mínimos y un resultado que pueda auditar."
results:
  - { label: "pruebas automáticas en verde", value: 405, source: "suite de la copia pública, Windows, 2026-10-11" }
  - { label: "latencia ssh al nodo por Ethernet (mediana de 10)", value: 187, unit: "ms", source: "tutorial 29 §8, 2026-10-10" }
next:
  - "Aprovisionar un nodo Linux: hoy solo hay Mac y Windows."
  - "Sumar el portátil como tercer nodo, sin fecha comprometida."
media:
  kind: video
  poster: /media/enjambre/poster.webp
  posterMobile: /media/enjambre/poster-mobile.webp
  video:
    mp4: /media/enjambre/preview.mp4
---

Una PC con Windows orquesta y una Mac mini con Apple Silicon ejecuta los trabajos de Claude Code sin
interfaz. La PC valida un contrato de tarea, elige la máquina con una tabla (`routing.toml`) y el
estado vivo, y manda el trabajo por SSH. El nodo crea un worktree en una rama propia, ejecuta el
agente dentro de `tmux` y deja un `result.json`. La PC trae la rama, la escanea en busca de secretos
y la sube; el registro `jobs.jsonl` anota nodo, rama, tiempos, código de salida y consumo.

El visor es un tablero local que lee la telemetría de cada nodo (CPU, memoria con presión, swap,
temperatura, red, procesos) y el registro de trabajos. Su modo demo corre el mismo código con un
enjambre sintético, así que se puede enseñar sin exponer ninguna máquina real.

Límites: solo Mac y Windows hoy; los trabajos desatendidos necesitan el llavero de macOS
desbloqueado tras un reinicio; los códigos de salida no verifican los criterios de hecho, así que
revisar la rama sigue siendo humano. Código bajo MIT; tipografías del visor bajo SIL OFL 1.1.
```

## `Landing/src/content/projects/en/enjambre.md`

```markdown
---
title: "enjambre"
segment: "agentes-ia"
tech: ["ia-agentes", "automatizacion", "infra-servidores"]
sector: ["herramientas-dev"]
order: 12
featured: false
status: "live"
maturity: "usable"
statusNote: "Open source · usable"
published: true
since: 2026
date: "2026-10-11"
summary: "Agentic coding spread across hardware I own: a Windows PC orchestrates and a Mac mini runs Claude Code jobs headless, each in its own git worktree. Every job comes back as a reviewable branch and one line in an append-only ledger, and a local visor shows nodes, jobs and telemetry."
oneLine: "I spread Claude Code jobs across my PC and a Mac mini; each one returns as a branch I review, with a live dashboard of the machines."
techLine: "Python · Bash · SSH · git worktrees · tmux · Claude Code"
repoUrl: "https://github.com/IchiSieben/enjambre"
demoUrl: "https://enjambre.ichisieben.dev"
skills:
  - "python"
  - "bash"
  - "git"
  - "llm-agentes"
  - "macos-arm"
  - "windows"
  - "sqlite"
applied:
  python: "Orchestrator, router, scheduler and visor on the standard library, with a 405-test suite."
  bash: "worker.sh runs on the node, compatible with macOS's bash 3.2, inside tmux so it survives an SSH drop."
  git: "Git is the bus: one worktree and one branch per job; nodes never push, the PC fetches the branch, scans it with gitleaks and pushes it."
  llm-agentes: "Headless Claude Code with an explicit tool allow-list, no --dangerously-skip-permissions and a task contract (allowed files and done criteria)."
  macos-arm: "A 16 GB Mac mini M2 Pro as the work node; the keychain is reached over SSH through a tmux started in the GUI session."
  windows: "The Windows PC is the orchestrator; in Git Bash every spawned process costs about 28 ms, which is why the Mac wins by 14 to 27 times on bash scripts."
  sqlite: "The visor keeps its own usage history, which is never deleted."
problem: "I have a PC and a Mac mini that are idle most of the time, and unattended coding agents tend to run with too many permissions or leave nothing reviewable behind. I wanted to spread work across both with minimal permissions and a result I can audit."
results:
  - { label: "automated tests passing", value: 405, source: "public copy suite, Windows, 2026-10-11" }
  - { label: "ssh latency to the node over Ethernet (median of 10)", value: 187, unit: "ms", source: "tutorial 29 §8, 2026-10-10" }
next:
  - "Provision a Linux node: today there is only Mac and Windows."
  - "Add the laptop as a third node, with no committed date."
media:
  kind: video
  poster: /media/enjambre/poster.webp
  posterMobile: /media/enjambre/poster-mobile.webp
  video:
    mp4: /media/enjambre/preview.mp4
---

A Windows PC orchestrates and an Apple Silicon Mac mini runs the Claude Code jobs headless. The PC
validates a task contract, picks the machine from a table (`routing.toml`) plus live state, and
sends the job over SSH. The node creates a worktree on its own branch, runs the agent inside `tmux`
and leaves a `result.json`. The PC fetches the branch, scans it for secrets and pushes it; the
`jobs.jsonl` ledger records node, branch, times, exit code and usage.

The visor is a local dashboard that reads each node's telemetry (CPU, memory pressure, swap,
temperature, network, processes) and the job ledger. Its demo mode runs the same code over a
synthetic swarm, so it can be shown without exposing any real machine.

Limits: Mac and Windows only today; unattended jobs need the macOS keychain unlocked after a
reboot; exit codes do not check the done criteria, so reviewing the branch is still a human step.
Code under MIT; the visor's fonts under SIL OFL 1.1.
```
