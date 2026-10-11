# Permitidos vs prohibidos

## Qué es

Dos formas de filtrar lo que sale de un sistema. Una **lista de permitidos** (allowlist) dice qué
se deja pasar y descarta todo lo demás. Una **lista de prohibidos** (denylist) dice qué se
bloquea y deja pasar el resto.

## Por qué importa aquí

La diferencia aparece el día que alguien añade un campo nuevo.

| Situación | Denylist | Allowlist |
|---|---|---|
| Se añade un campo `ruta_completa` al estado del visor | Sale a internet, salvo que alguien se acuerde de prohibirlo | No sale: nadie lo copió a propósito |
| Falla la lista | Falla abierto: se filtra algo | Falla cerrado: falta un dato en la web |

Una denylist exige imaginar de antemano todo lo que puede salir mal. Una allowlist solo exige
decidir qué sí se publica.

## Ejemplo real

La copia web del visor usa las dos, en capas (defensa en profundidad):

1. **`web.public_snapshot` (allowlist).** Cada campo se copia de forma explícita. Los nombres de
   proyecto se convierten en alias estables, los discos en `disco-N`, los montajes en `recurso-N`
   y tmux se reduce a un conteo.
2. **`leak_check` (denylist, segundo muro).** Si en lo ya redactado aparece una palabra `confidencial` u
   `reservado`, una cadena con forma de ruta, una IPv4 o un nombre privado, **bloquea la subida**.

El segundo muro bloqueó una carga real. La ruta SVG del logo de macOS contiene
`2.774.6.926.466`, que se lee como IPv4. No era una fuga, era un falso positivo, y señaló un dato
mal ubicado: los íconos de sistema operativo pasaron a la plantilla HTML y salieron de los datos.
Un muro que a veces se equivoca hacia el lado de bloquear es el costo aceptable de ese segundo
muro.

Detalle del caso: tutorial [24 — Visor v3, copia web y bandeja](../tutorial/24-visor-web-y-bandeja.md).

## Cuándo una denylist es aceptable

Solo como **segundo muro**, detrás de una allowlist. Sirve para atrapar el error del primer muro
(un campo permitido que contiene algo que no debía) y para que ese error sea ruidoso. No sirve
como única defensa: lo que no se previó, pasa.

## Error típico

- **Denylist como único filtro**, porque "es más fácil de escribir". Crece con cada incidente y
  nunca está completa.
- **Allowlist con comodines** (`copiar todo el diccionario`), que en la práctica es una denylist.
  Si el código hace `dict(estado)` y luego borra campos, no es una allowlist.
- **Silenciar el segundo muro** cuando da un falso positivo. Primero hay que preguntar por qué ese
  dato estaba donde estaba.

## Dónde leer más

- Tutorial [24 — Visor v3, copia web y bandeja](../tutorial/24-visor-web-y-bandeja.md), sección B.
- Ficha [Mínimo privilegio para agentes](minimo-privilegio-para-agentes.md): la misma idea
  aplicada a herramientas (`--allowedTools`).
