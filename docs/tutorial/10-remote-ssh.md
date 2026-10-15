---
lesson:
  route: build-stack
  order: 11
  level: 1
---

# 10 — Editar en Windows, ejecutar en la Mac (VS Code Remote-SSH)

> **Pendiente: estos pasos están escritos pero no se han ejecutado** (necesitan la interfaz
> gráfica de VS Code). No hay salidas ni tropiezos reales todavía; la sección "Qué salió mal" y las
> capturas se completan al ejecutarlos. Requisito previo: el ítem 1 de `NEEDS-OWNER.md` (llavero,
> ver `09-agente-desatendido.md`); si no, `claude` en la terminal remota dirá "Not logged in".

## Objetivo

Abrir en VS Code de Windows una carpeta que vive en la Mac, con su terminal, y correr `claude`
ahí: la barra de estado debe decir `@mac`.

## Por qué

Con dos escritorios tienes que mover la cabeza y las manos. Con Remote-SSH, la **interfaz**
(ventanas, editor, tu teclado) se queda en Windows y el **cómputo** (archivos, terminal,
compilación, Claude Code) corre en la Mac. VS Code instala un pequeño servidor en la Mac por SSH
y le habla por ese túnel: un escritorio, dos CPU. Bonus: Claude Code usa la configuración de
`~/.claude` **de la Mac**, así que verás su barra, no la de Windows (la etiqueta `@mac` la pone
la propia barra, ver `07-orquestador.md`).

## Pasos (en Windows)

1. VS Code → Extensiones (`Ctrl+Shift+X`) → buscar **Remote - SSH** (de Microsoft) → Instalar.
2. `F1` → **Remote-SSH: Connect to Host…** → aparece `mac` (lo lee de `~/.ssh/config`) → elegirlo.
3. Primera vez: pregunta la plataforma del host → **macOS**. Instala el servidor de VS Code en la
   Mac (unos segundos; no pide contraseña porque ya se usa llave, ver `03-ssh.md`).
4. **File → Open Folder…** → `~/Documents/Portfolio/enjambre`.
5. Terminal (menú Terminal → New Terminal): es una terminal **de la Mac**:
   ```
   uname -s     → Darwin
   claude       → la barra muestra @mac
   ```

## Qué salió mal de verdad

Nada todavía: no se ha ejecutado. Posibles tropiezos, **previstos pero no vividos**:

- Si la terminal remota no encuentra `claude` o `brew`: es el problema de `02-homebrew-y-shell.md`
  (el PATH tiene que estar en `~/.zshenv`).
- Si `claude` dice "Not logged in": la sesión de VS Code llega por SSH y no ve el llavero (ver
  `09-agente-desatendido.md`).

## Cómo verificar (criterio E3-1)

- [ ] `uname -s` imprime `Darwin` en la terminal integrada.
- [ ] `claude` abre y su barra muestra `@mac`.
- [ ] Captura: `Win+Shift+S` en Windows (la ventana es de Windows, así que aquí sí puedes
      capturar sin permisos de macOS); recortar usuario e IP antes de guardarla en `img/`.

## Autoevaluación

1. ¿Qué corre en Windows y qué en la Mac con Remote-SSH?
   <details><summary>Respuesta</summary>En Windows, la interfaz (editor, ventanas, teclado); en la
   Mac, el cómputo: archivos, terminal, Claude Code.</details>
2. ¿Qué configuración de Claude Code ves en la terminal remota?
   <details><summary>Respuesta</summary>La de `~/.claude` de la Mac, no la de Windows.</details>
3. `claude` en la terminal remota dice "Not logged in". ¿Por qué?
   <details><summary>Respuesta</summary>La sesión llega por SSH y no puede abrir el llavero de
   macOS donde está el login.</details>
