# 00 — Pantallas: un monitor ultrawide partido entre Windows y la Mac

## Objetivo

Ver el escritorio de Windows en la mitad izquierda del monitor y el de la Mac en la derecha, cada
uno con su resolución nativa de media pantalla (1720×1440).

## Por qué

Un monitor ultrawide de 3440×1440 se puede repartir entre dos equipos con el modo **Split screen**
(también llamado PBP, *picture by picture*): el propio monitor, no el sistema operativo, divide
el panel en dos entradas de video y cada mitad queda en 1720×1440. Para cada computadora eso es un
monitor distinto, de proporción casi cuadrada. Por eso el orden importa: Windows solo ofrece la
resolución de media pantalla **cuando el monitor ya está partido** (lo vivido aquí, ver
"Qué salió mal"). Para la Mac, el paso equivalente es elegir Extender.

> Alternativa sin partir la pantalla: un monitor con KVM USB (ver `01-deskflow.md`). El monitor
> de esta guía tiene 2 entradas DP y 2 HDMI y, según su ficha técnica, no trae hub USB ni KVM
> (dato verificado en `PLAN.md` §1.2); por eso aquí se usa el split.

## Pasos

1. En el menú del monitor (OSD, botones físicos): **Window settings** y elegir **Split screen**.
   El menú ofrece tres modos: *Single window*, *Split screen* y *Picture-in-picture*.

   *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

2. En Windows, **después** de activar el split: Configuración → Sistema → Pantalla →
   *Resolución de pantalla* → elegir la de media pantalla (1720×1440).
3. En la Mac: la mitad derecha mostrará *"Choose to Mirror or Extend Display from the menu"*.
   Ve a Centro de control → **Duplicar pantalla** y elige **Extender** (no Duplicar).

## Qué salió mal de verdad

**1. Cambiar la resolución antes de partir el monitor.** Antes del split, Windows muestra
3440×1440 como "Recommended". La captura muestra el comienzo de la lista (3440×1440, 2560×1440,
1920×1440, …); según la bitácora, 1720×1440 no aparece hasta activar el split en el monitor.
Arreglo: activar el split primero.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

**2. El escritorio de Windows como una caja dentro de la mitad.** Con la resolución que no
correspondía a media pantalla, el escritorio no llenó su mitad: quedó en una caja más pequeña
(las dos fotos; el recuadro gris es una zona tapada a propósito para ocultar nombres de archivo).
Diagnóstico: resolución distinta de la media pantalla real. Arreglo: elegir 1720×1440 una vez
activado el split.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

**3. La Mac "no detectaba" su mitad.** Sí la detectaba: el mensaje *"Choose to Mirror or
Extend Display from the menu"* es la Mac pidiendo que elijas qué hacer con la pantalla nueva.
Falta elegir **Extender**.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## Cómo verificar

- Windows: Configuración → Pantalla muestra la resolución elegida de media pantalla.
- Mac: Ajustes → Pantallas muestra el monitor extendido, no duplicado.
- Compartir teclado y mouse entre las dos mitades no es parte de este paso: es el siguiente
  (`01-deskflow.md`).

Pendiente de medir (en `PLAN.md` figura como "to test"): la frecuencia de refresco que
cada máquina reporta por mitad. El OSD de la captura muestra 180 Hz para el monitor completo; la
guía no midió qué recibe cada mitad.

## Autoevaluación

1. ¿Por qué Windows no ofrece 1720×1440 antes de activar el split?
   <details><summary>Respuesta</summary>Porque el split lo hace el monitor. Antes de activarlo,
   Windows solo ve un panel de 3440×1440; la mitad de pantalla aparece como resolución cuando
   el monitor la anuncia.</details>
2. La Mac dice "Choose to Mirror or Extend Display": ¿hay un fallo de conexión?
   <details><summary>Respuesta</summary>No. Detectó la pantalla; falta elegir Extender en Centro
   de control → Duplicar pantalla.</details>
3. ¿Qué cambia entre un KVM y el split con Deskflow?
   <details><summary>Respuesta</summary>Con KVM ves una máquina a la vez en toda la pantalla; con
   split ves las dos a la vez, y el teclado/mouse lo comparte software (Deskflow).</details>
