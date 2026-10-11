# Mínimo privilegio para agentes

## Qué es

Dar a un agente solo lo que necesita para la tarea, y que lo que no está permitido se **niegue** en vez de preguntarse. Con nadie mirando, una pregunta de permiso es un bloqueo; un permiso de más es un riesgo.

## Por qué importa aquí

Cuatro decisiones del proyecto:

1. **`--allowedTools` explícito.** El worker de la Mac solo puede leer, editar y usar `git status`, `git diff` y `ls`. Nada de `--dangerously-skip-permissions` en un nodo sin supervisión (`PLAN.md` §4.1, punto 3).
2. **Sin `sudo`.** La Mac no tiene sudo sin contraseña y no se configura.
3. **Patrón NEEDS-OWNER.** Lo que exige contraseña, un clic de macOS o un login se escribe en `NEEDS-OWNER.md` con los pasos exactos, y el agente sigue con otra cosa. Nunca pide la contraseña por chat.
4. **El llavero como frontera.** Una sesión SSH no abre el llavero de la sesión gráfica. Es una frontera deliberada: no se copian tokens ni se desbloquea el llavero desde un script (`security unlock-keychain` pide contraseña).

Honestidad: la sesión nocturna del orquestador en Windows se lanzó con `--allow-dangerously-skip-permissions` (captura 21 del índice). La regla rige para el worker desatendido de la Mac, no para esa sesión.

## Ejemplo real

```
allowed='Read,Edit,Write,Glob,Grep,Bash(git status:*),Bash(git diff:*),Bash(ls:*)'
```

```
ssh mac 'security show-keychain-info ~/Library/Keychains/login.keychain-db'
→ User interaction is not allowed.
```

## Error típico

Resolver el bloqueo del llavero copiando el token de Windows a la Mac o guardándolo en texto plano. `NEEDS-OWNER.md` §1 lista las dos salidas (token en archivo con `chmod 600`, o `tmux` iniciado desde la sesión gráfica) con su coste, y deja la elección al owner.

## Para saber más

- Permisos de Claude Code: https://docs.claude.com/en/docs/claude-code/iam
