# Capturas e imágenes

Todas las imágenes publicadas salen del modo demo (`python enjambre.py visor --demo`), que genera
un enjambre sintético con nombres, rutas y cifras inventados. Se graban en la Mac con Playwright.
Las capturas del entorno real no se publican: contienen rutas, nombres de máquina y direcciones
que la auditoría de publicación rechaza.

## Cómo generarlas

```bash
python enjambre.py visor --demo --serve    # lecturas nuevas cada 10 s; abrir state/visor/demo/visor.html
```

Resolución de captura: 1440 x 900 para las imágenes, 1280 x 720 para el video. Después de
generar cada archivo, abrirlo a tamaño real y comprobar que no aparece ningún nombre, ruta ni
dirección que no sea del demo.

## Lista

| Archivo | Qué muestra | Dónde se usa |
|---|---|---|
| `docs/img/visor-demo.png` | Vista general del visor: encabezado con el conteo de trabajos, los tres nodos (PC, Mac mini, Laptop), trabajos en curso y la tabla de atención | Portada de `README.md` y `README.en.md`; base del póster de la ficha del Landing |
| `docs/img/visor-nodos.png` | Pestaña de nodos: estado de presencia, CPU, RAM con presión y swap, temperatura, red | Sección "Qué mide y qué produce" |
| `docs/img/visor-trabajos.png` | Lista de trabajos con estado, nodo, rama, duración, consumo y causa de cada fallo; línea de tiempo | Documentación del visor |
| `docs/img/visor-telemetria.png` | Gráficas de las últimas 4 horas por nodo y procesos etiquetados con su trabajo | Documentación del visor |
| `docs/img/visor-demo.mp4` | Video de 15 a 30 s: recorrido de nodos, un trabajo que cambia de estado y las gráficas moviéndose (usa `--serve`) | Ficha del Landing (`preview.mp4`) |

Derivados para el Landing, a partir de los anteriores: `poster.webp` (1440 de ancho),
`poster-mobile.webp` (720 de ancho) y `preview.mp4`, copiados a `Landing/public/media/enjambre/`.

## Diagramas existentes

Ya están en el repositorio, en `docs/img/`, y no se regeneran:

- `despliegue.svg`: despliegue de nodos.
- `ida-y-vuelta.gif`: recorrido de un trabajo, renderizado desde la salida real de la ejecución.
- `secuencia-enjambre-run.svg`: secuencia de `enjambre run`.
- `ciclo-de-vida-tarea.svg`: estados de un trabajo.
- `red-lan.svg`: red local de los nodos.
- `mapa-de-historias.svg`: mapa de historias de usuario.

Antes de publicar, revisar cada uno por si lleva direcciones, nombres de máquina o rutas con
usuario; esos diagramas vienen del repo privado y no se verificaron en esta pasada. Los fuentes
Mermaid están en `docs/diagramas/`.

## Estado

Las cinco existen. Se grabaron en la Mac desde el modo demo y se revisaron a tamaño real. Sus
sha256 están en `tools/publicar/binarios.txt`: la auditoría rechaza cualquier binario que no
figure ahí.
