---
lesson:
  route: build-stack
  order: 10
  level: 2
---

# 09 — Un agente que trabaja solo toda la noche

## Objetivo

Dejar a Claude Code ejecutando varias fases de trabajo mientras duermes y, por la mañana, tener
un informe con evidencia de qué terminó, qué falló y qué solo tú puedes hacer.

## Por qué

Un agente desatendido tiene dos problemas que uno atendido no tiene: nadie le contesta si
pregunta, y nadie lo frena si se equivoca. La respuesta de esta guía a las dos cosas es
estructural, no de confianza:

1. **Reglas duras escritas antes** (qué no hacer nunca), para que el agente no tenga que decidir
   en caliente.
2. **Un archivo donde dejar lo que no puede hacer** (`NEEDS-OWNER.md`), para que un bloqueo no
   detenga la noche: se anota, se salta a la siguiente tarea independiente y se sigue.

Pero hay una advertencia que esta página documenta con un caso real: las reglas escritas en un
prompt solo valen lo que valga que el agente las cumpla. Aquí la sesión se lanzó con los
permisos de Claude Code **desactivados** (`--allow-dangerously-skip-permissions`), así que nada
técnico impedía romper una regla. Una la rompió, y lo declaró (ver "Qué salió mal").

Contraste con el paso 08: el worker que corre **en la Mac** nunca usa ese flag y lleva una lista
de herramientas permitidas. La sesión de Windows de esta noche sí lo usó; la seguridad dependió
de las reglas y de la revisión posterior.

## Pasos

### 1. Lanzar la sesión

En PowerShell, dentro del repo `enjambre`:

```powershell
PS> claude --allow-dangerously-skip-permissions
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

El primer mensaje (visible en la captura) le dijo, en resumen: que `gh auth login` ya estaba hecho
en la Mac, que las Command Line Tools se estaban actualizando, que ejecutara el prompt
`prompts/00-overnight-run.md`, que la Fase A ya estaba hecha (sesión 1), y que empezara en la
Fase B y siguiera hasta terminar todas las fases sin preguntar nada.

### 2. Las reglas duras del prompt

El prompt fija, por encima de cualquier otra instrucción (paráfrasis):

- Nada de `sudo` en la Mac; no teclear, pedir ni guardar contraseñas.
- **Ningún borrado** (archivos, ramas, repos), ningún `git push --force`, ningún
  `git reset --hard` sobre algo que no se creó esa noche.
- Ningún repo público nuevo ni cambio de visibilidad; los repos nuevos, solo privados y solo
  donde una fase lo pide.
- No pushear: el kit público (`claude-kit`, retenido hasta probar en la Mac), el repo del sitio
  web (su `main` se despliega solo), ni ningún repo cuya ruta contenga una palabra restringida.
- **gitleaks antes de cada push**; con un hallazgo, no se pushea y se anota.
- No tocar procesos que el agente no lanzó; los trabajos largos remotos, dentro de `tmux`.
- Si `ssh mac` falla: probar una vez un `ping` al nombre `.local` de la Mac para hallar la IP
  nueva (ya había cambiado una vez, ver `04-red-y-dhcp.md`); sin escanear la red.
- Si llega un límite de uso: escribir el estado en `HANDOFF.md` antes de parar.

### 3. El patrón NEEDS-OWNER

Todo lo que pide una contraseña, un clic de macOS, un login en navegador o una decisión del owner
se anota en `NEEDS-OWNER.md` con **exactamente qué clicar o correr**, se salta, y se sigue con lo
siguiente. En la mañana, el archivo es una lista ordenada de tareas humanas. Cada ítem trae qué
pasa, por qué, y las opciones con su costo.

### 4. El informe de la mañana

`MORNING-REPORT.md`, corto y en español, con cuatro bloques: lo que terminó **con evidencia**
(comando y resultado), lo que falló y por qué, los ítems NEEDS-OWNER en orden, y un plan para el
día. Lo que terminó esa noche (todo del informe):

| Fase | Resultado |
|---|---|
| B · orquestador en la Mac | 4 de 4 suites en verde; instalación idempotente; `verify.sh` sin deriva (ver `07-orquestador.md`) |
| C · walking skeleton | Ida y vuelta funcionando; camino de fallo con exit 1 (ver `08-primera-tarea-distribuida.md`); 12 tests OK |
| D · auditoría de respaldo | 59 repos auditados, **0 pushes** (ninguno cumplía todas las condiciones); 26 repos privados espejados en la Mac con `git bundle` |
| E · Drive y sitio | Solo documentación: los pasos por máquina quedaron en `NEEDS-OWNER.md` §6 |

Que la auditoría termine en **cero pushes** es un resultado correcto, no un fallo: se aplicó la
regla literal más las exclusiones del owner, y ningún repo quedó elegible.

## Qué salió mal de verdad

### El llavero de macOS no se abre por SSH

Lo más importante que descubrió la noche. El owner había hecho `gh auth login` en la Mac y
funcionó, pero el token quedó en el **llavero** (Keychain). Una sesión SSH no tiene acceso al
llavero de la sesión gráfica:

```
ssh mac 'security show-keychain-info ~/Library/Keychains/login.keychain-db'
→ User interaction is not allowed.
ssh mac 'gh auth status'           → The token in default is invalid.
ssh mac 'claude -p "ok" ...'       → "Not logged in · Please run /login"
```

La captura de `gh auth status` en la Terminal de la Mac (no por SSH) muestra el login válido,
con el almacenamiento `(keyring)`: la prueba de que el problema no es el login sino **dónde**
quedó guardado.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Consecuencias: `gh`, `git push` y `claude -p` no funcionan por SSH. Dos opciones documentadas
(el agente **no eligió** por el owner; siguen pendientes):

- **(a) Token en archivo.** `gh auth login --insecure-storage` y `claude setup-token` guardado en
  un archivo con `chmod 600`. Persiste tras reinicios, pero cualquiera que lea el disco tiene el
  token.
- **(b) `tmux` iniciado desde la sesión gráfica**, una vez por arranque. Lo que se lance desde
  SSH dentro de ese tmux hereda el contexto gráfico y ve el llavero. Sin texto plano, pero hay
  que repetirlo al reiniciar.

Y una prohibición: **nunca** `security unlock-keychain` desde un script, porque pide la
contraseña.

### Los tropiezos del propio agente (contados tal cual en el informe)

- **Colisión de nombres de log.** Una colisión entre nombres de log obligó a re-ejecutar los
  tests.
- **En zsh, la variable `path` pisa `PATH`.** En zsh, `path` es un arreglo ligado a `PATH`:
  asignarle algo en un bucle reescribe el `PATH`. Un bucle del agente dejó sin comandos a esa
  sesión SSH.
- **El primer push al repo de pruebas salió sin gitleaks previo.** Rompió la regla "gitleaks
  antes de cada push". Se escaneó después: limpio. El escaneo posterior no sustituye al previo.
- **Un nombre de equipo real viajó a dos ramas de prueba.** `result.json` incluía el `hostname`
  de la Mac en dos ramas del repo de pruebas (privado). Se corrigió para las siguientes
  (ver `08-primera-tarea-distribuida.md`); esas dos ramas no se reescribieron.
- **Borró archivos propios pese a la regla "ningún borrado".** Logs y bundles que el propio
  agente había creado esa noche en `/tmp` de la Mac. No tocó nada del owner, pero la regla era
  absoluta, y por eso lo declaró.

Un contraejemplo de la regla bien aplicada: las 4 carpetas que quedaron a medias tras un primer
intento de clonado **no se borraron**; se completaron con un segundo `fetch`
(`docs/DECISIONES.md` §15).

### Decisiones sin el owner

Cada vez que el agente decidió algo por su cuenta, lo anotó en `docs/DECISIONES.md` con qué
eligió, por qué, qué descartó y cómo revertirlo. Ejemplo: se pushea desde Windows la rama que la
Mac no puede pushear, en vez de copiar un token (decisión 9).

## Cómo verificar

Para cada fila del informe, reproduce su comando y compara con el resultado anotado. Y la
afirmación central, que el llavero bloquea SSH, se reproduce así:

```powershell
PS> ssh mac 'security show-keychain-info ~/Library/Keychains/login.keychain-db'
```

Resultado esperado: `User interaction is not allowed.` Si dejó de pasar, ya se aplicó una de
las dos opciones (a) o (b).

Pendiente de revisar a mano, porque no hay comando que lo pruebe solo: que la lista de reglas
duras se cumplió. Lo que el propio agente declaró romper está en la sección anterior; lo que no
declaró, no está verificado.

## Autoevaluación

1. ¿Por qué un bloqueo no detiene la noche?
   <details><summary>Respuesta</summary>Porque se anota en `NEEDS-OWNER.md` con las instrucciones
   exactas y el agente salta a la siguiente tarea independiente.</details>
2. La sesión se lanzó sin permisos. ¿Qué protegía entonces de un `rm -rf`?
   <details><summary>Respuesta</summary>Solo la regla escrita en el prompt y la revisión
   posterior; nada técnico lo impedía. Por eso hay que revisar el informe y las reglas duras
   deben ser pocas y claras.</details>
3. `gh auth status` dice "valid" en la Terminal de la Mac y "invalid" por SSH. ¿Qué cambia?
   <details><summary>Respuesta</summary>El token está en el llavero, que solo abre la sesión
   gráfica; SSH no tiene acceso.</details>
