---
lesson:
  route: build-stack
  order: 16
  level: 2
---

# 17 — Transcripción con hablantes: medir antes de creer

## Objetivo

Audio → texto con marcas de tiempo y "Hablante 1, 2…" → renombrar una vez y que
el nombre se propague a SRT, VTT y TXT. Medido en audios de 5, 20 y 60 min.

## Por qué

Transcribir ya es fácil; saber **quién** habló es lo difícil (*diarización*). Son
dos modelos distintos: Whisper escucha palabras, el diarizador agrupa trozos de voz por
parecido. El pipeline los cruza por tiempo. Y un número de error solo vale si sabes qué
respuesta era la correcta: por eso se mide con audio cuya verdad conoces.

Repo: `IchiSieben/modelos-mac`. Requiere el gestor del paso 16 corriendo en la Mac.

## Pasos

### 1. Audio de prueba con verdad conocida

No se usaron los vídeos personales del dueño: tienen gente identificable. Se generan diálogos
en español con voces de síntesis y se guarda quién dijo qué y cuándo:

```
mac$ python scripts/make_synthetic.py --minutes 60 --voices Mónica,Paulina,kokoro:em_alex,kokoro:ef_dora --name real-60
```

Deja `~/modelos/audios/real-60.wav` (60,1 min) y `real-60.truth.json` (cada turno con su voz,
inicio, fin y texto).

`kokoro:*` son voces del modelo Kokoro-82M (Apache-2.0), vía `mlx-audio`. Solo para generar
pruebas: su fonetizador arrastra una dependencia GPL, así que vive en un extra aparte
(`--extra synth`), fuera del servicio.

### 2. El pipeline

```
audio ──ffmpeg──► WAV 16 kHz mono ──► whisper-large-v3-turbo ──► segmentos con texto
                                  └─► Nemotron-3-Diarization ──► turnos (quién, cuándo)
                         merge: cada segmento toma el hablante con más solape → "Hablante N"
```

Los hablantes se numeran por orden de aparición. El renombrado no toca el resultado original:
se guarda un mapa `{"Hablante 1": "Moderadora"}` y se aplica al exportar.

### 3. Desde la web

Túnel (`ssh -L 8765:127.0.0.1:8765 mac`) y en el navegador `http://127.0.0.1:8765/`: *Tarea:
Transcripción con hablantes*, subir el archivo, *Encolar*. El audio de 5 min tardó **19,0 s**
(carga de modelos 3,2 s, footprint 2,9 GB). Clic en la fila, escribir los nombres,
*Renombrar*:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

El SRT ya sale con los nombres:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

### 4. Medir

```
mac$ ~/modelos/envs/main/bin/python scripts/bench_speakers.py ~/modelos/audios/real-{05,20,60}.wav
```

| Audio | Voces | Tiempo | × tiempo real | Horas de audio por hora | Footprint | Error de hablante (DER) | Hablantes hallados | Error de palabras (WER) |
|---|---|---|---|---|---|---|---|---|
| 5,2 min | 2 | 18,2 s | 0,059 | 17,0 | 2,93 GB | 5,3 % | 2 ✔ | 0,6 % |
| 20,0 min | 3 | 51,3 s | 0,043 | 23,4 | 3,56 GB | 4,6 % | 3 ✔ | 1,6 % |
| 60,1 min | 4 | 147,3 s | 0,041 | **24,5** | 5,34 GB | 8,8 % | 4 ✔ | 0,2 % |

**~24 horas de audio por hora** en audios largos. En el de 60 min, Whisper se lleva 135 s y la
diarización solo 12 s. Cada segmento mal asignado queda listado en
`bench/speakers-<audio>.review.txt` para revisarlo a mano.

### 5. Dos diarizadores, uno por defecto

| | Nemotron (por defecto) | sherpa-onnx (alternativa) |
|---|---|---|
| 60 min | 147 s, DER 8,8 %, **4** hablantes | 829 s, DER 7,2 %, **13** hablantes |
| Dónde corre | GPU (MLX) | CPU (ONNX) |
| Licencia | OpenMDW | MIT + Apache |

Error parecido, pero Nemotron acierta **cuántas** personas hay, que es lo que hace útil el
renombrado, y es 5,5× más rápido. Riesgo: en un audio de 5 min dio **36,6 %** de error (en otro
con las mismas dos voces, 3,0 %). Si el resultado se ve raro: `{"diarizer": "sherpa", "num_speakers": N}`.

### 6. Por qué Nemotron falló con 36,6 %

Nemotron no agrupa voces al final, como sherpa. Procesa el audio en trozos y tiene **8 casillas
de hablante** que se llenan **por orden de llegada**: un caché de hablantes. Lo que se decide al
principio pesa en todo lo demás. Se cargó el modelo una vez y se probaron variantes sobre el mismo
audio de 5 min (Mónica + Eddy es-MX, voces distintas):

| Variante | Error (DER) | Probabilidad máx. del 2.º hablante |
|---|---|---|
| Audio completo (2 corridas) | 36,6 % y 36,6 % | 0,53 |
| Umbral 0,3 / 0,2 en vez de 0,5 | 22,9 % / 13,3 % | 0,53 |
| Sin los primeros 5 s | 31,6 % | 0,84 |
| Sin los primeros 15 s | 14,4 % | 0,94 |
| Sin los primeros 30 s | **3,6 %** | 0,96 |

Tres conclusiones:
- **No es azar**: el mismo audio da siempre el mismo error.
- **Lo decide el arranque.** Lo que el modelo oye en los primeros ~30 s condiciona el caché:
  la segunda voz acaba en la casilla 0 y la casilla 1 nunca gana confianza. Si el audio empieza
  después de esos 30 s, separa bien. Qué parte exacta del arranque lo provoca no se aisló: ahí la
  segunda voz tiene un turno corto, pero quitar los primeros 15 s (que lo quitan entero) todavía
  deja 14,4 %.
- **Bajar el umbral es un parche**: mejora el error, pero no lo arregla.

Consecuencia práctica: si el resultado tiene casi todo el tiempo en un solo hablante, desconfía.
Repite con sherpa, que agrupa el audio entero al final y no depende del arranque.

### 7. Con un audio real (56 min)

Un audio real del dueño, de 56 min, con gente real. El audio y las transcripciones **no salen de
la Mac** ni entran a ningún repo: quedan en `~/audios-prueba/salidas/`. Se encola desde la propia
Mac:

```
mac$ curl -s -F task=speakers -F 'params={"diarizer":"nemotron"}' -F "file=@$HOME/audios-prueba/<audio>.m4a" 127.0.0.1:8765/jobs
mac$ curl -s "127.0.0.1:8765/jobs/<id>/export?fmt=srt" > ~/audios-prueba/salidas/nemotron.srt
```

| Diarizador | Tiempo | Horas de audio por hora | Footprint pico | Hablantes hallados |
|---|---|---|---|---|
| Nemotron | 158 s | 21,2 | 5,25 GB | 3 (47 % · 52 % · 1 % del tiempo) |
| sherpa, umbral 0,5 | 616 s | 5,5 | 5,26 GB | **129** |
| sherpa, `num_speakers: 3` | 629 s* | 5,3 | 5,26 GB | 3 (8 % · 92 % · 0 %) |

\* Esta corrida compartió la Mac con otra prueba; el tiempo limpio de sherpa es el de la fila de
arriba.

Lo que se ve sin conocer la respuesta correcta:
- **Whisper rinde igual que con voces sintéticas**: 129–146 s para 56 min.
- **sherpa, sin decirle cuántas personas hay, es inútil con audio real**: 129 grupos.
- **Los dos diarizadores no coinciden**: discrepan en el **48 %** del tiempo, medido tomando uno
  como referencia del otro. De eso, ~38 % es *quién* habla y ~11 % es que uno oye voz donde el
  otro no. Nemotron reparte el tiempo casi a mitades; sherpa con 3 hablantes
  fijados pone el 92 % en uno. Con lo que sabemos del paso 6, ninguno de los dos merece
  confianza a ciegas.

El número 3 se lo dio Nemotron a sherpa. Por eso la cantidad de hablantes no es una opinión
independiente, pero el reparto de quién habla cuándo sí lo es. Cuál acierta lo decide una persona
leyendo los dos SRT.

### 8. Que el gestor arranque solo: LaunchAgent

```
mac$ bash scripts/install-launchagent.sh
.../Library/LaunchAgents/dev.modelos.server.plist: OK
{"version":"0.1.0","running":null,"task":null,"queued":0,"free_gb":54.3}
mac$ lsof -nP -iTCP:8765 -sTCP:LISTEN
Python  94703 <usuario>  7u  IPv4  TCP 127.0.0.1:8765 (LISTEN)
```

Lo que hace el LaunchAgent:
- Arranca el gestor al iniciar sesión en la pantalla (`LimitLoadToSessionType: Aqua`).
- Si se cae, lo relanza (`KeepAlive`). Probado: se mató el proceso por su PID y volvió solo a
  los ~21 s.
- Sigue escuchando solo en `127.0.0.1`.
- Un trabajo de 5 min corrió igual que antes: 18,7 s y 2,93 GB.

El instalador es idempotente; `--uninstall` lo quita.

## Qué salió mal de verdad

1. **Mis audios de "3 y 4 hablantes" tenían 2 voces.** La primera tanda dio 37–50 % de error en
   los dos diarizadores y parecían malos. La matriz de confusión mostraba que **los dos
   fusionaban los mismos pares**. Causa: macOS solo trae dos voces en español (Mónica y
   Paulina); cualquier otro nombre (Eddy, Reed…) cae a una de esas y el audio sale **byte a
   byte idéntico** (mismo sha1). Los modelos tenían razón y mi verdad estaba mal. Arreglo: voces
   de Kokoro. Lección: comprobar que los datos de prueba son lo que dicen ser antes de culpar al
   modelo.
2. **Maté mi propia síntesis de 60 min.** El bucle de espera hacía `tmux kill-session` al
   vencer su plazo, terminara o no el trabajo. Se regeneró; desde entonces se espera con un
   bucle `until` que comprueba el resultado y nunca mata nada.
3. **sherpa-onnx instalaba pero no cargaba**: le faltaba `libonnxruntime.dylib`, que viene en
   `sherpa-onnx-core`.
4. **Kokoro: "Model type tts not supported"**: `mlx-audio` deduce el tipo por el nombre de la
   carpeta. `~/modelos/tts` → `~/modelos/kokoro`.
5. **El primer intento con el audio real murió por la GPU**:
   `[METAL] Command buffer execution failed: Discarded (victim of GPU error/recovery)
   (…InnocentVictim)`. *Innocent victim* quiere decir que la GPU se reinició por culpa de
   otro y nuestro trabajo cayó con ella. No se pudo saber quién: el registro del sistema no se
   lee por SSH. Fotos estaba abierta. El segundo intento, idéntico, terminó bien. El gestor
   marca el trabajo como `failed` con el error completo, y basta con reencolarlo.
6. **El LaunchAgent arrancaba y se quedaba dormido**: 7 MB, 0 % de CPU, sin abrir el puerto ni
   escribir el log. Causa: lanzado por `launchd`, Python leía el repo en `~/Documents`. macOS
   protege esa carpeta (privacidad, TCC) y el proceso queda esperando un permiso que nadie ve.
   Desde tmux no pasa; lo más probable es que ahí el permiso venga heredado de la terminal (no se
   verificó aparte). Arreglo: el instalador copia
   `server/` a `~/modelos/app`, fuera de `~/Documents`, y el agente corre esa copia. Así no hay
   que darle a Python acceso a todos tus documentos. Coste: después de cambiar el código hay
   que volver a correr el instalador.

## Cómo verificar

```
mac$ lsof -nP -iTCP:8765 -sTCP:LISTEN
Python  94703 <usuario>  7u  IPv4  TCP 127.0.0.1:8765 (LISTEN)
mac$ ~/modelos/envs/main/bin/python scripts/bench_speakers.py ~/modelos/audios/real-{05,20,60}.wav
```

Esperado (tabla del paso 4): audio de 5,2 min en unos 18 s, 2 hablantes hallados, DER 5,3 %,
WER 0,6 %; el de 60,1 min, 4 hablantes hallados, DER 8,8 %, cerca de 24 horas de audio por hora.
El gestor responde en `127.0.0.1:8765` y sigue vivo tras matar su proceso (vuelve a los ~21 s).
Los tiempos son techo, no promedio (ver Límites).

## Límites (léelos antes de vender esto)

- Las tablas de los pasos 4 a 6 son con voces sintéticas: sin ruido, sin solapes, sin
  interrupciones. El audio real del paso 7 lo confirma: allí los dos diarizadores discrepan en
  casi la mitad del tiempo.
- n = 1 por audio. La Mac indexaba Fotos de fondo (60–320 % de CPU): los tiempos son el techo.

## Autoevaluación

1. ¿Por qué Whisper solo no sabe quién habla?
2. Un diarizador da DER 7 % pero encuentra 13 hablantes donde hay 4. ¿Por qué es peor para el
   usuario que uno con DER 9 % y 4 hablantes?
3. ¿Qué habrías revisado primero al ver que dos modelos distintos fallan exactamente igual?
