# Pull vs push

## Qué es

Dos formas de mover un mensaje entre dos máquinas. En **push**, el emisor llama al receptor
cuando hay algo (un webhook). En **pull**, el receptor pregunta cada cierto tiempo si hay algo
nuevo (sondeo). La diferencia práctica: quién tiene que ser alcanzable desde fuera.

## Por qué tiramos aquí

La bandeja vive en un hosting; el que ejecuta está en casa. Para empujar, la PC o la Mac tendrían
que aceptar conexiones entrantes: un puerto abierto o un túnel. No queremos ninguno.

- **Sin puertos de entrada.** Nada escucha en casa. La PC hace una petición saliente, que el
  router ya permite.
- **Un hosting comprometido no da camino a casa.** Quien lo controle puede escribir tareas falsas,
  pero no tiene por dónde entrar; y la PC revisa cada tarea con sus propias reglas.
- **El costo es la latencia.** La PC pregunta cada 2 min dentro de `watch`, así que una tarea
  puede tardar hasta 2 min en verse como "recibida". Para un trabajo que dura minutos u horas, es
  barato.

Es el mismo reparto de [Sondeo vs latido](sondeo-vs-latido.md): aquí también pregunta Windows.

## Cuándo es mejor push

- Cuando la latencia importa (segundos) y el receptor ya es público, como un servicio en la nube.
- Cuando el sondeo sería caro: muchas consultas casi siempre vacías contra algo que cobra por
  petición.
- Cuando hay un canal entrante ya seguro y mantenido (VPN propia, túnel administrado) y su
  superficie de ataque ya está aceptada.

## Error típico

- **Sondear demasiado rápido** por impaciencia: más carga y más ruido, sin ganancia real.
- **Tratar el pull como si fuera confianza:** que la PC inicie la conexión no hace confiable lo
  que recibe. Hay que verificar la firma y volver a aplicar todas las reglas del lado que ejecuta.
- **Olvidar la repetición:** un mensaje recogido dos veces se ejecuta dos veces. Aquí la tarea se
  marca vista *antes* de encolar y las firmas llevan marca de tiempo monótona.

## Dónde leer más

- Tutorial [24 — Visor v3, copia web y bandeja](../tutorial/24-visor-web-y-bandeja.md).
- Diseño de la bandeja: [BANDEJA.md](../BANDEJA.md), sección 2.
- Ficha [Cola de trabajos](cola-de-trabajos.md).
