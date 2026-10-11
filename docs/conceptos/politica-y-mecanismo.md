# Política y mecanismo

## Qué es

Separar **qué se quiere** (la política) de **cómo se hace cumplir** (el mecanismo). La política
se escribe donde un humano la lee y la cambia; el mecanismo es código pequeño que la aplica sin
opinar. Viene del diseño de sistemas operativos (Hydra, 1974): el núcleo da mecanismos, las
políticas se enchufan encima.

## Por qué importa aquí

Con agentes, el texto es una política que el modelo **casi siempre** sigue. "Casi siempre" no
alcanza para algo que no quieres que pase nunca. Por eso cada regla importante del enjambre
tiene las dos mitades:

| Política (texto que se lee) | Mecanismo (código que obliga) |
|---|---|
| `routing.toml`: qué tipo de trabajo va a qué máquina | `routing.py`: funciones puras que deciden y explican |
| `~/.claude/rules/enjambre.md`: "delega por enjambre, no con `ssh mac`" | `hooks/enjambre-guard.sh`: frena el `ssh mac` a pelo |
| `AGENTS.md`: qué puede tocar un worker | `enjambre.validate`: sin `AGENTS.md`, `run` se niega |

## Ejemplo real

Sesión 13 (2026-10-07). La regla cargada en una sesión nueva le dice que use enjambre. Le pedí a
propósito `ssh mac echo hola` y el hook respondió con su mensaje, y el comando no corrió:

```
PreToolUse:Bash hook error: [bash ~/.claude/hooks/enjambre-guard.sh]: enjambre-guard: `ssh mac` a pelo está bloqueado fuera de enjambre/orquestador.
```

## Error típico

- **Solo política:** confiar en que el modelo lee la regla. Funciona hasta el día en que el
  contexto está lleno, la regla quedó lejos y el atajo parece razonable.
- **Solo mecanismo:** un hook que bloquea sin explicar. El agente reintenta por otro lado (otra
  herramienta, otro alias). El mensaje del hook tiene que decir **qué usar en su lugar** y cómo
  saltárselo a conciencia (`ENJAMBRE_RAW_SSH=1`, que queda registrado).
- **Un mecanismo que falla cerrado:** si el hook se rompe y bloquea todo, nadie puede trabajar.
  Este falla abierto: sin `jq` o con un JSON raro, deja pasar.

## Dónde leer más

- Tutorial [23 — El mandamiento](../tutorial/23-mandamiento.md).
- Wulf et al., "HYDRA: The Kernel of a Multiprocessor Operating System" (CACM, 1974), la
  separación original.
- Documentación de hooks de Claude Code (`PreToolUse`, código de salida 2).
