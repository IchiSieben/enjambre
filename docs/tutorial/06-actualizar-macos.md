---
lesson:
  route: build-stack
  order: 7
  level: 0
---

# 06 — Actualizar macOS (y qué se rompe y qué no)

## Objetivo

Pasar la Mac de macOS 15.3.1 a una versión que admita Xcode 27, y comprobar después, por SSH,
que las herramientas del paso 02 siguen respondiendo.

## Por qué

Una actualización mayor de macOS toca todo lo que está debajo de tus herramientas: el sistema,
las Command Line Tools, la red. Por eso conviene saber **antes** qué cambia y tener un comando
que lo mida **después**. Aquí el motivo de actualizar fue concreto: la ficha de Xcode en la App
Store exige una versión mínima de macOS.

## Pasos

1. Ver el requisito. En la App Store, la ficha de Xcode (versión 27.0) dice
   *"Requires macOS 26.6 or later"*.

   *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

2. Ajustes del Sistema → General → Actualización de software (*Software Update*). La captura
   muestra *"Downloading macOS 27.0.1…"* con la barra de progreso. Es un paso a mano (el owner,
   con la interfaz gráfica; una actualización mayor reinicia la Mac).

   *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

   Las fuentes no explican por qué se instaló 27.0.1 y no 26.6, el mínimo que pedía Xcode.

3. Cuando la Mac vuelva, actualizar también las Command Line Tools (el mismo panel de
   actualización de software las ofrece; ver "Qué salió mal").

## Qué salió mal de verdad

- **Discrepancia de versión entre bitácora y realidad.** Los apuntes manuales dicen
  macOS **15.3.1** (build 24D70); el comando en vivo, más tarde, dijo **27.0.1** (build 26A434).
  La Mac se había actualizado entre una cosa y otra. Regla: manda lo que imprime `sw_vers` hoy,
  no lo anotado ayer.
- **Las Command Line Tools quedaron obsoletas.** `brew doctor` avisó
  *"Your Command Line Tools are too outdated"* (registrado en el HANDOFF de la sesión 1). El
  arreglo es instalar la actualización de las CLT desde Actualización de software; en la sesión
  de la corrida nocturna el owner indicó que "las Command Line Tools se están actualizando".
- **Efecto colateral en la red.** Tras la actualización, la IP de la Mac cambió y `ssh mac`
  dio *Connection timed out*: ver `04-red-y-dhcp.md`. Las fuentes no prueban que la actualización
  *causara* el cambio; solo que ocurrió después.

## Cómo verificar

Mismo comando antes y después, desde Windows:

```powershell
PS> ssh mac 'sw_vers; claude --version; brew --version'
```

| | Antes (bitácora) | Después (`sw_vers` en vivo, 2026-10-04) |
|---|---|---|
| macOS | 15.3.1 (24D70) | 27.0.1 (26A434) |
| Claude Code | 2.1.289 | 2.1.289 |
| Homebrew | 7.0.7 | 7.0.7 |

Las herramientas instaladas no cambiaron de versión con la actualización (Homebrew y Claude Code
iguales antes y después) y respondieron por SSH.

Pendiente de comprobar tras actualizar, porque las fuentes no lo registran: que `brew doctor` ya
no avise de las CLT, y que Deskflow reconecte solo (`01-deskflow.md`).

## Autoevaluación

1. ¿Qué versión de macOS pedía la ficha de Xcode 27.0?
   <details><summary>Respuesta</summary>macOS 26.6 o posterior.</details>
2. La bitácora dice 15.3.1 y `sw_vers` dice 27.0.1. ¿A cuál le haces caso?
   <details><summary>Respuesta</summary>A `sw_vers`: es la medición actual; la bitácora es de antes
   de actualizar.</details>
3. Después de actualizar, `brew doctor` se queja de las Command Line Tools. ¿Qué haces?
   <details><summary>Respuesta</summary>Instalar la actualización de las CLT desde Ajustes →
   General → Actualización de software.</details>
