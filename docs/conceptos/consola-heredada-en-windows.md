# Consola heredada en Windows

**Qué es.** En Windows, cada programa de línea de comandos (`ssh.exe`, `ping.exe`, `python.exe`)
necesita una **consola**: la ventana negra donde escribe. Al arrancar no la crea de cero:
**hereda la de su padre**. Si el padre no tiene consola, Windows le crea una nueva y la
**muestra**.

`pythonw.exe` es Python *sin* consola. Para eso existe: correr un vigilante en segundo plano
sin que aparezca nada. Pero cada `ssh.exe` que lanza no tiene consola que heredar, así que
Windows le abre una ventana, aunque sea por medio segundo. Con un sondeo cada 30 s, eso es una
ventana negra cada 30 s.

| Cómo se lanza el hijo | Qué pasa |
|---|---|
| Desde una terminal abierta | Hereda esa consola: no aparece nada nuevo |
| Desde `pythonw`, sin banderas | Windows crea una consola **visible**: la ventana que parpadea |
| `creationflags=CREATE_NO_WINDOW` | El hijo tiene consola, pero **sin ventana** |
| `+ startupinfo` con `SW_HIDE` | Si el hijo crea su propia ventana igual, nace oculta |

Las dos banderas hacen falta porque no todos los `ssh.exe` son iguales. El primero del `PATH`
en esta máquina es el de Git para Windows (`C:\Program Files\Git\usr\bin\ssh.exe`), que trae
una capa de compatibilidad POSIX (msys) y puede abrir una consola propia por su cuenta.
`CREATE_NO_WINDOW` cubre la consola que da Windows; `SW_HIDE` cubre la que pueda crear el
programa.

## En enjambre

Un solo lugar define las banderas: `presence.no_console()`. Devuelve
`{creationflags, startupinfo}` en Windows y `{}` en cualquier otro sistema, así el mismo código
corre en la Mac. Lo usan todas las llamadas a `subprocess` de `presence.py` y `enjambre.py`.
`tools/visor/build.py` y `collect.py` llevan una copia porque se ejecutan solos. Un test
(`NoConsole.test_every_windows_ssh_call_site_hides_its_console`) recorre esos archivos y falla si
aparece una llamada nueva sin `no_console()`.

La otra mitad del arreglo es **lanzar menos procesos**. `watch` hace ping cada 30 s, y el
sondeo caro (SSH + `tmux`) solo cuando cambia la respuesta del ping o cada 5 min
(`presence.GatedProbe`). Además, la dirección que da `ssh -G`, que es otro `ssh.exe`, se resuelve
una sola vez.

## Cómo comprobarlo sin mirar la pantalla

Hay que muestrear cada 0,5 s los procesos que descienden de `pythonw.exe` y preguntar a Windows si
alguno tiene una ventana visible (`EnumWindows` + `IsWindowVisible`). Un "no vi nada" mirando el
monitor no prueba nada, porque el parpadeo dura menos de un segundo.

## Errores típicos

- **Poner la bandera en un solo sitio.** El vigilante tenía `CREATE_NO_WINDOW` en `presence.py`,
  pero `visor/build.py`, `collect.py` y el `ssh` de los trabajos no.
- **Probar desde una terminal.** Ahí el hijo hereda la consola y nunca parpadea: el bug solo
  existe bajo `pythonw` o una Tarea Programada.
- **Arreglarlo con `cmd /c start /min`.** La ventana aparece igual, solo que minimizada, y en
  la barra de tareas.

**Alternativa:** correr el vigilante como servicio de Windows (sesión 0, sin escritorio). No
hay ventanas posibles, pero tampoco toasts, y los servicios necesitan permisos de administrador
para instalarse.
