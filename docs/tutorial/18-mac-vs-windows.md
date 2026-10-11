# 18 — Mac vs Windows con las mismas tareas, y modelo local vs `claude -p`

**Objetivo:** saber con números qué conviene mandar a la Mac: tests, build, git, un trabajo real
de `claude -p`, y si un modelo local de 9B reemplaza a `claude -p` para código.

**Por qué:** "la Mac es más rápida" no sirve para decidir. Hay que separar dos cosas que el
reloj mezcla: el tiempo del **modelo en la nube** (igual en cualquier máquina) y el tiempo
**local** (arrancar Node, hooks, servidores MCP, lanzar procesos). Solo el segundo depende de
la máquina.

Máquinas: Mac mini M2 Pro 16 GB · Windows 10, Intel i5-8600, 48 GB, Git Bash.

## Pasos

### 1. Mismo código en las dos: `git bundle`

```
PS> git -C ..\orquestador bundle create <bundles>\orquestador.bundle --all
PS> git -C ..\enjambre    bundle create <bundles>\enjambre.bundle --all
PS> scp <bundles>\*.bundle mac:~/modelos/benchcmp/
PS> python scripts\bench_compare.py <bundles> <work> windows --claude
mac$ python scripts/bench_compare.py ~/modelos/benchcmp ~/modelos/benchcmp/work mac --claude
```

Los dos lados clonan del mismo bundle: mismos commits (orquestador `89ae2fd`, enjambre
`6a14b6e`), sin depender de GitHub ni del llavero.

### 2. Herramientas locales (mediana de 3)

| Tarea | Windows | Mac | Mac más rápida |
|---|---|---|---|
| Tests orquestador: statusline | 15,40 s | 1,08 s | 14× |
| Tests orquestador: hooks | 17,59 s | 1,03 s | 17× |
| Tests enjambre (Python, 38) | 0,55 s | 0,35 s | 1,6× |
| Build `build-kit.sh` | 16,98 s | 0,62 s | 27× |
| git clone local | 0,24 s | 0,03 s | 8× |
| git status ×20 | 1,42 s | 0,14 s | 10× |
| git gc --aggressive | 1,88 s | 0,85 s | 2,2× |

La diferencia enorme está en los scripts de **bash**; en Python puro, apenas 1,6×. Hipótesis
coherente con eso (no medida aparte): en Windows cada `jq`, `sed` o `date` pasa por la
emulación de `fork` de MSYS, que es cara, y estos tests lanzan cientos de procesos.

### 3. `claude -p`: la nube por un lado, lo local por otro

El JSON de `claude -p --output-format json` trae `duration_api_ms`. Local = reloj − API:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

| | Reloj | Nube | Local | Coste |
|---|---|---|---|---|
| Windows | 45,4 s | 15,0 s | **30,4 s** | US$ 0,28 |
| Mac | 15,1 s | 11,1 s | **4,0 s** | US$ 0,18 |

¿La máquina o la configuración? Windows tiene 13 servidores MCP y la Mac 8. Con un prompt
trivial, 2 corridas por celda:

| Arranque local | Windows | Mac |
|---|---|---|
| Normal | 18,2 / 18,7 s | 4,1 / 3,6 s |
| Sin MCP (`--strict-mcp-config`) | 13,6 / 12,9 s | 1,8 / 1,6 s |

Los MCP suman ~5 s en Windows; **el resto (~13 s contra ~1,7 s) es la plataforma**.

### 4. Modelo local de código vs `claude -p`

Tres funciones con tests ocultos que ninguno de los dos ve: montos en soles, unir intervalos,
dígito verificador del RUC. El local tiene un intento y **un reintento** con la salida de los
tests; `claude -p` es un agente que puede escribir y correr sus propias pruebas.

```
PS> python scripts/bench_code.py            # local sin razonamiento + claude -p
PS> python scripts/bench_code.py --think --skip-claude
```

| Motor | Aciertos | Tiempo | Coste |
|---|---|---|---|
| `claude -p` (Opus 5.5) | **3/3** | 132 s | US$ 0,91 |
| Qwen3.5-9B local | 1/3 | 155 s | US$ 0 |
| Qwen3.5-9B local con *thinking* | 1/3 | 751 s | US$ 0 |

Sin maquillaje: para código con reglas exactas, el 9B local **no reemplaza** a `claude -p`.
Acertó `merge_intervals` a la primera; falló la agrupación de miles y el dígito verificador
aun con reintento. Razonar más lo hizo 5× más lento sin acertar más.

## Qué salió mal de verdad

1. **`build-kit.sh` no corre en la Mac**: el `sed` de BSD rechaza la línea 55
   (`RE error: parentheses not balanced`, un `\)` sin `\(`). Se midió con GNU sed
   (`brew install gnu-sed`); el bug quedó anotado para arreglarlo en orquestador.
2. **"2 fallos en Windows" que no eran de Windows**: dos tests de statusline buscan la sesión
   por directorio y el clon de prueba vivía en una ruta temporal rara. En el checkout normal
   del mismo commit: 50/0.
3. **`claude -p` desde Python en Windows recibía la mitad del prompt**: `claude` es un `.cmd` y
   `cmd.exe` corta los argumentos en el primer salto de línea. Arreglo: el prompt va por stdin.
4. **Borrar el clon de prueba fallaba en Windows**: los *packs* de git son de solo lectura.
   Arreglo: quitar el atributo y reintentar dentro de `rmtree`.

## Límites

n pequeño (1 corrida del trabajo real por máquina). Carga ajena: Fotos indexando en la Mac,
navegador y otra sesión de Claude en Windows. Los tiempos son techo, no promedio.

## Autoevaluación

1. ¿Por qué se resta `duration_api_ms` del reloj y no al revés?
2. Si quitas todos los MCP, ¿cuánto del hueco Windows–Mac queda? ¿Qué te dice eso?
3. ¿Qué tipo de tarea le darías hoy al modelo local, y cuál nunca?
