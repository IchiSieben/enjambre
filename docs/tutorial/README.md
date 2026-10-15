# Windows + Mac con Claude Code: guía de configuración

Para quien tiene **una PC con Windows y una Mac** (Apple Silicon) y quiere usar las dos como un
solo puesto de trabajo: la Windows orquesta, la Mac ejecuta, y Claude Code hace por SSH todo lo
que no exige una contraseña o un clic en un permiso de macOS.

Cada página trae: **Objetivo**, **Por qué**, **Pasos**, **Qué salió mal de verdad** (el error
real, con captura y arreglo), **Cómo verificar** y **Autoevaluación**. Las salidas están
redactadas: `<usuario>`, `<ip>` y `mac` en lugar de los valores reales. Lo que no se ha
ejecutado aparece marcado como **Pendiente**; nada se inventa.

| Paso | Qué se logra | Quién lo hace | Estado |
|---|---|---|---|
| [00 — Pantallas](00-pantallas.md) | Monitor ultrawide partido: Windows a la izquierda, Mac a la derecha | Tú (a mano) | hecho |
| [01 — Deskflow](01-deskflow.md) | Un teclado y un mouse para las dos máquinas | Tú (permisos de macOS) + Claude Code (arranque automático) | a medias; el cursor cruza y arranca solo en las dos; faltan las cinco pruebas de encendido y el portapapeles (owner, NEEDS-OWNER §8) |
| [02 — Homebrew y shell](02-homebrew-y-shell.md) | Homebrew, Claude Code, `gh`, PATH que sirve también por SSH | Tú (instalador con sudo) + Claude Code verifica | hecho |
| [03 — SSH](03-ssh.md) | `ssh mac` sin contraseña | Tú (sudo) + Claude Code verifica | a medias; llave: hecho; apagar la contraseña: pendiente |
| [04 — Red y DHCP](04-red-y-dhcp.md) | Misma subred; IP que no cambie | Tú (módem) | a medias; misma red: hecho; reserva DHCP: pendiente |
| [05 — Respaldo verificado](05-respaldo-verificado.md) | Copia de fotos con sha256 en disco externo | Claude Code en la Mac | hecho; copia y espejo en Windows verificados (0 diferencias); liberar más de 150 GB en la Mac sigue abierto |
| [06 — Actualizar macOS](06-actualizar-macos.md) | macOS 15.3.1 a 27.0.1 y qué cambió | Tú (Actualización de software) | hecho |
| [07 — El kit en la Mac](07-orquestador.md) | Repos por `git bundle`, port, tests, config en capas | Claude Code | hecho |
| [08 — Primera tarea distribuida](08-primera-tarea-distribuida.md) | `enjambre run`: Windows manda, vuelve una rama + ledger | Claude Code | hecho; con worker `claude` real desde el paso 11 |
| [09 — Agente desatendido](09-agente-desatendido.md) | Una noche de trabajo sin supervisión, con reglas e informe | Claude Code | hecho |
| [10 — Remote-SSH](10-remote-ssh.md) | VS Code en Windows, cómputo en la Mac | Tú (interfaz gráfica) | pendiente; pasos escritos, sin ejecutar (necesita la interfaz de VS Code) |
| [11 — El llavero por SSH](11-llavero-y-tmux.md) | `claude`, `gh` y `git` por SSH con un tmux que nace en tu sesión; primer trabajo real | Claude Code (sin clics tuyos) | hecho; reinicio verificado el 4 oct |
| [12 — La Mac para 3D y para estudiar](12-mac-para-3d-y-estudio.md) | Blender en Metal: STL → PNG con una línea desde Windows; Onshape → render; algoritmos en las dos máquinas | Claude Code (exportar de Onshape: tú) | hecho |
| [15 — Presencia y cola](15-presencia-y-cola.md) | `enjambre watch` sabe qué nodos están listos; los trabajos a un nodo caído esperan en cola | Claude Code | hecho; notificaciones sin ver en pantalla |
| [16 — Modelos locales en la Mac](16-modelos-locales.md) | Cinco modelos MLX con sha256, servidos de a uno por un gestor que Windows usa por túnel | Claude Code | hecho |
| [17 — Transcripción con hablantes](17-transcripcion-con-hablantes.md) | Audio → texto con "Hablante N" renombrable; ~24 h de audio por hora, medido con verdad conocida | Claude Code (audio real: tú) | hecho; sintético + un audio real de 56 min (qué diarizador acierta lo decide el owner) |
| [18 — Mac vs Windows](18-mac-vs-windows.md) | Mismas tareas en las dos máquinas; nube vs local en `claude -p`; modelo local de código vs Claude | Claude Code | hecho; una sola corrida del trabajo real por máquina |
| [19 — Discos compartidos](19-discos-compartidos.md) | La Mac monta carpetas de Windows por SMB con un usuario dedicado; montaje automático, `watch` lo vigila, firewall | Claude Code (administrador: tú) | a medias; propuesta y script de montaje listos; los bloques A y D del owner (usuario SMB, firewall) no se han ejecutado, así que el montaje no se ha probado |
| [20 — Visor maestro](20-visor-maestro.md) | Una página con nodos, sesiones abiertas, consumo, cola y comparativa por tipo de tarea; resumen en la statusline | Claude Code | hecho |
| [21 — Modelos locales, ronda 2](21-modelos-ronda-2.md) | Catálogo, banco de pruebas y elección automática de modelo por tarea, medidos en la Mac | Claude Code | hecho; quedan decisiones y dos mediciones del owner |
| [22 — El enrutador](22-enrutador.md) | `enjambre run` sin `--node` elige la máquina con una tabla (`routing.toml`) y el estado vivo, y explica por qué en una línea | Claude Code | hecho; ejecutor en Windows pendiente (`win` sigue con `runnable = false`) |
| [23 — El mandamiento](23-mandamiento.md) | Toda sesión de Claude Code, en las dos máquinas, sabe que la Mac existe; `ssh mac` a pelo se bloquea con un hook; `/mac` deja `AGENTS.md` + `DELEGAR.md` | Claude Code | hecho; reiniciar sesiones abiertas y pegar la regla en dos ventanas: owner (NEEDS-OWNER §16) |
| [24 — Visor v3, copia web y bandeja](24-visor-web-y-bandeja.md) | Visor con dos costos (equivalente API y cuota) e historial propio que no se borra; copia web redactada con lista de permitidos; bandeja para dejar tareas desde el celular que la PC recoge, valida y encola | Claude Code (subdominio, contraseña y claves: tú) | hecho; ya desplegado en `visor.ichisieben.dev`; falta la prueba de la bandeja desde el celular (owner) |
| [25 — Trabajos largos](25-trabajos-largos.md) | `enjambre script` (comando declarado en AGENTS.md, $0) y `run --detach` (agente con tope en USD): vuelven al instante, laten, se reanudan si mueren y dejan una línea en el ledger; `kind: tests` con exactamente `Bash(<tests-cmd>)` | Claude Code (reiniciar `watch`: tú) | hecho; 3 pruebas de fuego con números reales |
| [26 — El puente](26-puente.md) | Un Claude Code persistente en la Mac al que Windows le habla (`enjambre chat/send/sessions`), con Playwright, ledger por turno y `gui sí/no` en status; modelo de amenazas de la clave | Claude Code (login de Claude y permiso de Grabación de pantalla en la Mac: tú) | hecho; prueba de fuego pasada con modelo; sin probar Remote Control ni la recogida automática de turnos de `send` por `watch` |
| [27 — Visor v3](27-visor-v3.md) | Cada fallo con su causa y agrupado; pruebas aparte; proyectos ordenables por gasto desde el celular; línea de tiempo con zoom; consumo con lentes y mapa de calor; modelos locales con logo, estado y nota editables | Claude Code (aprobar alias y estados de modelos: tú) | hecho y desplegado en la copia web; aprobar alias y estados de modelos: owner |
| [28 — Visor en vivo y telemetría](28-telemetria.md) | Lo que corre ahora, arriba y en verde, cada 10 s (`enjambre live --serve`); por nodo: CPU, GPU, RAM con presión y swap, térmico, red, gráficas de 4 h y procesos etiquetados con su trabajo o sesión | Claude Code (temperatura con sudo: tú) | hecho en el visor local; temperatura en grados de la Mac: opcional, requiere sudo del owner |
| [29 — Prioridades](29-prioridades.md) | Lo urgente pasa delante sin matar lo que espera: reglas en un archivo, admisión, prioridad del sistema operativo y preempción en el tick de `live --serve` | Claude Code | hecho; tres decisiones del owner abiertas (NEEDS-OWNER §24) |
| [30 — Arranque desatendido](30-arranque.md) | Que el enjambre vuelva solo tras un reinicio: inventario de lo que arranca, `ready` solo cuando `claude` puede trabajar, tareas `--boot`, ruta /32 para la VPN | Claude Code (reinicios y sudo: tú) | a medias; los resultados del reinicio real y la prueba con la VPN encendida están pendientes del owner |
| [31 — Pulido del visor](31-pulido-visor.md) | Cada número del visor dice de cuándo es y de dónde sale; la causa de un fallo sin el nombre de usuario | Claude Code | hecho; la pestaña Proyectos aún muestra el nombre de la carpeta de inicio |
| [32 — Arranque ciego](32-arranque-ciego.md) | Un trabajo que no puede correr falla en el acto y dice por qué; `clone`, `--ref`, `dead-at-launch`, `logs`, `cerrar` | Claude Code | hecho; el `watch` residente necesita reiniciarse para usarlo |

Convención: los bloques que empiezan con `PS>` se corren en PowerShell de Windows; los que
empiezan con `mac$` se corren en la Terminal de la Mac.

> Regla de oro de toda la guía: **nunca pegues de una vez un bloque de comandos que pide
> contraseña.** Las líneas siguientes entran como si fueran la contraseña (tropiezo real en
> [03](03-ssh.md)). Un comando a la vez.
