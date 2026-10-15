---
lesson:
  route: build-stack
  order: 13
  level: 2
---

# 12 — La Mac para 3D y para estudiar

## Objetivo

Desde Windows, una línea renderiza en la Mac cualquier pieza STL del proyecto integrador y trae
el PNG. Además, la Mac corre las verificaciones del curso de algoritmos igual que Windows.

## Por qué

La Mac mini M2 Pro tiene una GPU de 16 núcleos que Blender usa por **Metal**, la API gráfica de
Apple. En Windows este trabajo competiría con todo lo demás; en la Mac corre aparte, sin pantalla,
mientras sigues trabajando. Hay dos ideas que conviene tener claras:

- **Un render no necesita un modelo de IA.** Es un trabajo **determinista**: mismo STL, misma
  imagen. Por eso no pasa por `claude`, cuesta $0 y lo lanza un script, no un agente.
- **STL no guarda unidades.** Es una lista de triángulos con números sin unidad. Blender lee
  1 unidad = 1 metro; casi todos los CAD, Onshape incluido, exportan en milímetros. Si no
  corriges la escala, una base de 230 mm llega como un objeto de 230 m.

## Pasos

### 1. Qué hay en el módulo mecánico

Repo del equipo (remoto de un compañero), carpeta `Proyecto/MóduloMecánico/`:

| Archivo | Triángulos | Tamaño (caja envolvente) | Formato |
|---|---|---|---|
| `Estacion_v2_Base_v2.stl` | 4 846 | 229,5 × 229,5 × 73,2 | STL binario, 237 KB |
| `Estacion_v2_windmill_v4.stl` | 3 010 | 80 × 80 × 110 | STL binario, 147 KB |

Los números solo tienen sentido en **milímetros**: una estación de 23 cm y un rotor de 11 cm.
La caja la calculé leyendo el STL binario (cabecera de 80 bytes, un entero con el número de
triángulos y 50 bytes por triángulo). El resto del repo: el esquema electrónico en PDF y un
`main.ino` (Arduino/ESP32).

### 2. Llevar el repo a la Mac

```bash
PS> ssh mac 'mkdir -p ~/Documents/Portfolio/laptop && bash ~/.enjambre/tmux-run.sh -C ~/Documents/Portfolio/laptop -- git clone -q https://github.com/<compañero>/Proyecto-3d.git'
PS> ssh mac 'cd ~/Documents/Portfolio/laptop/Proyecto-3d && shasum -a 256 Proyecto/MóduloMecánico/*.stl'
fedde192…0a6ed90948a05eeaf  Proyecto/MóduloMecánico/Estacion_v2_Base_v2.stl
a54f0e90…751d3af74a1d0daa  Proyecto/MóduloMecánico/Estacion_v2_windmill_v4.stl
```

Mismo `sha256` que en Windows: son los mismos bytes. El `git clone` va por `tmux-run.sh` porque
necesita el llavero (página 11).

### 3. Instalar Blender

```bash
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -- bash -c "brew list --cask blender >/dev/null 2>&1 && echo ya-estaba || brew install --cask blender"'
🍺  blender was successfully installed!
PS> ssh mac '/Applications/Blender.app/Contents/MacOS/Blender --version | head -1; lipo -archs /Applications/Blender.app/Contents/MacOS/Blender'
Blender 5.2.2 LTS
arm64
```

`arm64` y nada más: es un binario nativo de Apple Silicon, sin Rosetta. Ocupa 907 MB. No pidió
contraseña: un cask que va a `/Applications` no necesita `sudo` si tu usuario es administrador.

### 4. El script de render

`tools/render_stl.py` se ejecuta **dentro** de Blender, sin interfaz:

```bash
blender -b --factory-startup -P render_stl.py -- PIEZA.stl SALIDA.png --device METAL --samples 64
```

- `-b` es sin ventana; `--factory-startup` ignora tus preferencias, así el resultado no depende
  de cómo tengas configurado Blender.
- Importa con `global_scale=0.001` (`--unit mm`, el valor por defecto).
- Centra la pieza y coloca la cámara **a partir de la caja envolvente**: la misma cuenta encuadra
  un tornillo o un chasis.
- Cycles con `--device METAL` (GPU) o `CPU`, 1280×720, 64 muestras con eliminación de ruido.
- La última línea es un JSON con los tiempos.

### 5. CPU contra Metal

Medido el 4 oct, M2 Pro, 1280×720, 64 muestras + denoise, cada combinación dos veces:

| Pieza | Metal (GPU 16 núcleos) | CPU (10 núcleos) | Metal es |
|---|---|---|---|
| Base (4 846 triángulos) | 3,61 s · 3,58 s | 10,62 s · 10,59 s | 2,9× más rápido |
| Molino (3 010 triángulos) | 3,57 s · 3,64 s | 10,86 s · 10,82 s | 3,0× más rápido |

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

*Render de la salida real con `freeze`.*

Los tiempos son solo del render (sin el arranque de Blender) y casi no varían entre repeticiones.
**Ojo con el primer render en Metal: tardó 80 s** con solo 16 muestras. Cycles compila sus
kernels para tu GPU la primera vez y los guarda; desde la segunda, 3,6 s. Si mides, descarta
siempre la primera corrida.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

### 6. Una línea desde Windows

```bash
PS> python enjambre.py render "laptop/Proyecto-3d/Proyecto/MóduloMecánico/Estacion_v2_Base_v2.stl"
{"id": "render-Estacion-v2-base-v2-094837", "worker": "blender", "status": "ok",
 "png": "...\\enjambre\\state\\renders\\render-Estacion-v2-base-v2-094837.png",
 "render": {"device": "METAL:Apple M2 Pro (GPU - 16 cores)", "samples": 64, "triangles": 4846,
            "size_m": [0.2295, 0.2295, 0.0732], "render_s": 3.69, ...}, "cost_usd": 0}
```

9,5 s de punta a punta la primera vez; 13 s desde que también copia el STL desde Windows. Qué hace:

1. Copia `tools/render_stl.py` a la Mac (siempre la versión revisada aquí).
2. Si el STL **existe en Windows**, lo copia a la misma ruta en la Mac. Así sirve también para
   una pieza recién exportada de Onshape que todavía no está en git. Probado con un STL que solo
   existía en Windows, en una ruta con espacios: `"copied_from_win": true`, render en 4,0 s.
3. Corre Blender dentro del tmux de la sesión gráfica: si se corta el SSH, el render sigue.
4. Trae el PNG a `state/renders/` y escribe una línea en el ledger (`worker: blender`).

Opciones: `--device CPU`, `--samples 128`, `--res 1920x1080`, `--unit m`, `--out <carpeta>`.

¿Por qué `enjambre render` y no `enjambre run`? `run` es el contrato de un **agente que edita un
repo** y devuelve una rama. Un render no edita nada. Además, este repo es de un compañero: una
rama de trabajo se empujaría a **su** GitHub. (DECISIONES 51.)

### 7. De Onshape a la imagen

Onshape corre en el navegador, así que la exportación la haces tú; lo demás es una línea.

1. **Exportar.** En el *Part Studio*, clic derecho sobre la pieza en la lista de piezas →
   **Export**. Para render: formato **STL**, *Format* **Binary** (más pequeño que *Text*),
   *Units* **Millimeter**, *Resolution* **Fine** (o *Custom*: tolerancia de cuerda, desviación
   angular y ancho mínimo de faceta). Para guardar el diseño (otro CAD, archivo): **STEP**, que
   lleva geometría exacta y no triángulos. **Ojo: el STEP de Onshape sale en metros por
   defecto**; marca *Use custom units for export* → **Millimeter** si lo quieres en mm.
   Fuente: [Onshape Help — Exporting Files](https://cad.onshape.com/help/Content/File/exporting_files.htm)
   (consultada el 4 oct 2026; no lo probé en la interfaz).
2. **Guardar en el repo**, con el nombre de versión que ya usa el equipo:
   `Proyecto/MóduloMecánico/<Pieza>_v<N>.stl`. El STEP, si lo quieres, al lado.
3. **Renderizar:** `python enjambre.py render "laptop/Proyecto-3d/Proyecto/MóduloMecánico/<Pieza>_v<N>.stl"`.
4. **Comprobar la escala:** `size_m` del JSON debe coincidir con la medida de Onshape. Si
   Onshape dice 229,5 mm, `size_m` debe decir `0.2295`. Si dice `229.5`, exportaste en metros
   o importaste sin escala: repite con `--unit m`, o reexporta en milímetros.

| Exportaste en | Pasa | Ejemplo |
|---|---|---|
| mm (lo normal) | `--unit mm` (por defecto) | 229,5 mm → `size_m` 0.2295 |
| m | `--unit m` | 0,2295 m → `size_m` 0.2295 |
| pulgadas (*Inch*) | aún no soportado: reexporta en mm | — |

Blender no abre STEP sin complementos. Para el render, siempre STL.

### 8. Algoritmos en la Mac

El curso no trae dependencias: los scripts usan solo la biblioteca estándar de Python, y el fuzz
del visualizador usa Node. En la Mac faltaba Node:

```bash
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -- brew install node'
PS> ssh mac '/opt/homebrew/bin/node --version'
v26.10.0
```

(En Windows es v22.14.0; el fuzz pasa en las dos.) El repo `ada-pc2` se clonó en
`~/Documents/Portfolio/algoritmos/ada-pc2`; `parcial/` no es un repo git, así que se copiaron
solo sus verificaciones (`parcial/verificacion/`).

Las mismas verificaciones en las dos máquinas (108 comprobaciones del parcial, el fuzz del
visualizador de QuickSelect y la regeneración del sitio), medidas con un cronómetro en Python:

| Paso | Windows (Python 3.12.10, Node 22) | Mac M2 Pro (Python 3.12.15, Node 26) |
|---|---|---|
| `verificar_tarjetas.py` (27 OK) | 68,9 s | 35,8 s |
| `verificar_pd5.py` (18 OK) | 13,2 s | 6,5 s |
| Las otras 5 (63 OK) | 7,6 s | 3,4 s |
| **Total Python, 108/108 OK** | **89,7 s** | **45,7 s** |
| Fuzz de particiones (Node) | 1,5 s | 0,2 s |
| `deploy/build.py` | no se corrió (reescribe `deploy/` en tu copia) | 32 páginas, luego `git checkout -- deploy/` |

La Mac va casi el doble de rápido en Python de un solo núcleo. Ojo con la comparación: Windows
corrió una hora antes que la Mac (11:29), con `mediaanalysisd` de macOS analizando fotos en
segundo plano (más de 100 % de CPU). Las mediciones finas de Mac contra Windows están en la página
18, de otra sesión.

### 9. Un trabajo de estudio por la cola

Siguiente tarea pendiente de `ada-pc2` (ítems 7–11 de `SONNET-TODO.md`: etiquetas de inducción ya
redactadas) lanzada como trabajo normal:

```bash
PS> $env:ENJAMBRE_MAX_TURNS=20; python enjambre.py run --node mac --repo algoritmos/ada-pc2 --task tasks/TASK-ada-induccion-labels.md --worker claude
{"id": "ada-induccion-labels", "branch": "mac/ada-induccion-labels", "exit": 0, "status": "incomplete",
 "permission_denials": [{"tool": "Bash", "target": "python3 - <<'EOF' ..."}], "duration_s": 40, "cost_usd": 0.35}
```

40 s y $0,35. El agente aplicó los ítems 7, 8, 9 y 11 y saltó el 10, que no tenía dónde ir (la frase
`Hipótesis inductiva` no existe en esos archivos: lo comprobé). Marcó `incomplete` porque **primero
intentó** un script de Python por Bash, que no está permitido, y luego hizo lo mismo con `Edit`.
Lección: `incomplete` dice "hubo una denegación", no "falta trabajo". Hay que leer la rama.

## Qué salió mal de verdad

1. **80 s el primer render en Metal.** No era lentitud: era la compilación de kernels. Se ve en
   que la segunda corrida tarda 3,6 s con 4 veces más muestras.
2. **Primer render lavado** (fondo y pieza casi del mismo gris). Bajé la luz del fondo (0,6 →
   0,35) y oscurecí el material.
3. **Un JSON con mi usuario de macOS.** La línea del ledger traía la ruta absoluta del PNG en la
   Mac. El ledger no está en git, pero lo corregí igual: el script ahora devuelve solo el nombre.

## Cómo verificar

```bash
PS> python enjambre.py render "laptop/Proyecto-3d/Proyecto/MóduloMecánico/Estacion_v2_windmill_v4.stl"
PS> python enjambre.py status      # última línea: render-… mac blender ok
```

Abre el PNG de `state/renders/`: la pieza debe verse entera, centrada y con sombra.

## Autoevaluación

1. Importas un STL y Blender lo muestra 1000 veces más grande que en Onshape. ¿Qué pasó?
   <details><summary>Respuesta</summary>STL no guarda unidades. Onshape exportó en milímetros y
   Blender interpretó cada unidad como un metro. Se corrige escalando por 0,001
   (<code>--unit mm</code>).</details>
2. Mides Metal y el primer render tarda 80 s. ¿Concluyes que Metal es más lento que la CPU?
   <details><summary>Respuesta</summary>No. La primera corrida incluye compilar los kernels de
   Cycles para la GPU. Se descarta y se mide desde la segunda: 3,6 s contra 10,6 s.</details>
3. ¿Por qué el render no es un trabajo de <code>claude</code>?
   <details><summary>Respuesta</summary>Porque es determinista: no hay nada que decidir. Pagar
   un modelo para correr un comando fijo cuesta dinero y añade variación. El modelo se reserva
   para el juicio.</details>
