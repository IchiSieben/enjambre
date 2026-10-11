# 27 — Visor v3: cada fallo con su causa, proyectos ordenables y modelos con veredicto

**Qué consigues:** abrir el visor (en la PC o desde el celular) y saber, sin abrir una terminal,
por qué falló cada trabajo, cuánto gastó cada proyecto y en qué aparato, a qué hora del día
trabajas, y qué modelos locales sirven y cuáles no.
**Quién lo hace:** Claude Code (sesión F3, 2026-10-07). Tú apruebas los alias y los estados de los
modelos (NEEDS-OWNER §21).
**Antes:** [24 — Visor v3, copia web y bandeja](24-visor-web-y-bandeja.md) (F2). El acceso
directo de Windows está en [20 §4b](20-visor-maestro.md).

## Antes y después

La misma instantánea, en la misma copia web y a 390 px (celular):

| Antes (F2) | Después (F3) |
|---|---|
| *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)* | *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)* |

En F2 la tarjeta de atención decía «fallido» y nada más: para saber qué pasó había que entrar
por ssh y buscar el log. En F3 cada fallo lleva su **por qué**, en una línea, y los fallos con la
misma causa se agrupan («2 fallidos · exit 2: … unrecognized arguments: --id»).

## 1. Cada fallo con su causa (parte 1)

**Por qué:** el ledger es solo-anexar y guardaba el estado, pero no la razón. Una alerta sin causa
obliga a investigar a mano y, al tercer fallo igual, ya nadie la mira.

- `razones.py` escribe una frase de causa para cada trabajo fallido o incompleto: permisos
  denegados, tope de gasto, matado, `ssh` 255, la línea de error del log, el resultado del agente
  o la nota del push. También guarda la ruta del log y si es una prueba.
- `append_ledger` enriquece cada fila nueva. Las filas viejas se rellenaron una sola vez, con
  copia previa en `state/jobs.jsonl.bak-20261007-213211`:

```
PS> python razones.py --backfill
{"rows": 53, "changed": 28, "bad": 18, "bad_without_reason": 0, "bad_with_log": 9, "tests": 15}
```

- Las **pruebas** (sandbox, stubs, pruebas de fuego `e3-*`, smoke) se cuentan aparte y plegadas:
  fallar es su trabajo (DECISIONES 230).
- La cuota sin dato reciente se muestra en gris con «último dato hace N h», en vez de desaparecer.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## 2. Aparatos con color propio y alias públicos (parte 2)

- Cada aparato tiene su color, según el sistema operativo: PC azul, Mac mini gris claro y Linux
  ámbar. Se usa en las etiquetas, las barras y los gráficos (DECISIONES 228).
- La columna **Aparato** de proyectos reparte las horas activas entre PC y Mac.
- En la web, los proyectos salen con un **alias** (`config/visor-alias.toml`) y no con el nombre de
  la carpeta. Los 17 alias los propuso Claude y esperan tu aprobación (DECISIONES 229). El visor
  local muestra siempre el nombre real.

## 3. Proyectos, línea de tiempo y consumo (parte 3)

- **Proyectos:** tabla ordenable por cualquier columna (USD, sesiones, horas, tokens, último uso)
  y filtrable por texto, aparato y fecha. El filtro queda en la URL (`#ps=cost&pd=-1`), así que
  se puede guardar o compartir.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

- **Línea de tiempo** rehecha: ancho real, horas de Lima, carriles con nombre, zoom con rueda,
  pellizco o arrastre, y resaltado en los dos sentidos (al pasar sobre una barra se marca su fila
  y al revés). Comprobada a 1920 y a 390 px:

| 1920 px | 390 px | 390 px con zoom |
|---|---|---|
| *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)* | *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)* | *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)* |

- **Consumo:** un solo gráfico con cuatro lentes (8 días, día, semana, mes) en lugar de tres
  tablas, más un **mapa de calor** día de la semana × hora. Cada sesión reparte su costo entre las
  horas de reloj en que estuvo abierta. Lima tiene un desfase entero sin horario de verano, así que
  no hay que corregir horas partidas.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

- **«Equivalente evitado» no se calcula**: el gestor no guarda tokens ni minutos de audio por
  trabajo. La tarjeta muestra el conteo, la fórmula y qué dato falta (DECISIONES 231).

## 4. Modelos locales como inventario vivo (parte 4)

**Por qué:** había decenas de modelos en la Mac y ninguna forma de anotar cuáles valen la pena. La
decisión vivía en la memoria del owner.

- Cada modelo muestra el logo y el color de su familia (Gemma, Qwen, Mistral, Whisper…), su
  nombre real de Hugging Face, su estado («en uso / sirve / por probar / no sirve / descartado»)
  y una nota.
- Se filtra por tarea y por estado. El gráfico de calidad compara los modelos de una misma tarea
  y hay un ranking. Las tablas largas quedan plegadas.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)* *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

- **Editar el estado** desde el visor: el botón hace `PATCH /catalog/<key>` al gestor de la Mac
  por el túnel. Sin túnel, muestra el comando equivalente:

```
PS> python enjambre.py modelo <key> --status "sirve" --nota "una línea, hasta 200 caracteres"
```

Imprime la respuesta del gestor y sale con código 0 solo si el gestor respondió 200. La ruta del
túnel se probó de verdad desde una página `file://` (`diar`, guardado sin cambios).

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

El estado vive en `~/modelos/estado.json` de la Mac, fuera de git (DECISIONES 233).

## 5. Movimiento con propósito y modo en vivo (parte 5)

- Los números cuentan hacia arriba al cambiar, las filas nuevas aparecen con un fundido y «hace
  N s» avanza cada segundo. Con `prefers-reduced-motion` no hay animación.
- El botón **en vivo** (en la app de Windows) recarga cuando hay una instantánea nueva.
  `python enjambre.py visor --follow` reconstruye cada 30 s.
- El pie dice cada cuánto se sube la copia web (10 min).
- Tope de peso: la página puede crecer como máximo un 20 % sobre F2 (162 247 B → tope 194 696 B).
  Quedó en 188 353 B, y lo vigila un test (DECISIONES 227).

## 6. Despliegue

No existe `enjambre publicar`, y el deploy de archivo comprimido de Hostinger reemplazaría el
Landing. Por eso se subió solo `index.html`, por TUS, y antes se guardó la versión anterior
(DECISIONES 234):

```
rollback  state/web-dist/rollback/index-f2-20261007.html  162 512 B  sha 764a1de530fe
nuevo     public_html/visor-app/index.html                188 618 B  sha eb88477111d5
sin credenciales:  /  401   /data.php  401   /pwa/manifest.webmanifest  200
```

## El fallo que encontramos

Después del deploy, la subida de los datos se bloqueó sola:

```
2026-10-07T22:19:16 ... | web: BLOQUEADA: IP | ...
```

El detector de fugas busca IPs con una expresión `n.n.n.n`, y los trazados SVG de los logos de
modelos tienen números como `1.01.1.223`. No se filtraba ninguna IP real; el detector hizo su
trabajo y falló cerrado. Arreglo: los logos pasan por una **lista blanca** (`glyph_ok`: solo un
`<path d>` con datos de trazado, o la placa de monograma de 1 a 3 letras), y el detector revisa
todo lo demás, nombres de logo incluidos (DECISIONES 235). La subida siguiente pasó (171 072 B).

**Lección:** un detector por patrón sobre texto libre choca tarde o temprano con datos que no son
texto. Mejor validar la forma de esos datos y sacarlos del barrido, que aflojar el patrón para
todos.

## Comprobación

```
PS> python -m unittest discover -s tests
Ran 230 tests in 11.851s
OK (skipped=1)
```

Página desplegada con la carga real simulada, a 390 px: ningún error de JS, sin scroll
horizontal, y ordenar por USD pone arriba el proyecto de mayor gasto.
