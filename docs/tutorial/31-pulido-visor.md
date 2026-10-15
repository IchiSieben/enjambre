---
lesson:
  route: build-stack
  order: 30
  level: 2
---

# 31 — Pulido del visor: qué es «ahora», qué es ventana

## Objetivo

Que cada número del visor diga de cuándo es y de dónde sale, y que ninguna captura
enseñe el nombre de usuario.

## Por qué

Un gauge sin hora parece «ahora» aunque la lectura tenga 7 minutos (el ssh falló y se
muestra la última buena). Una curva sin ventana no dice si son 15 minutos o 4 horas. Y un contador
que no suma lo mismo que las tarjetas de debajo hace dudar de todo lo demás. Es la diferencia
entre **dato** y **metadato**: el número solo sirve si viaja con su hora y su origen.

Sigue a [28 — Telemetría](28-telemetria.md) y [29 — Prioridades](29-prioridades.md).

## Pasos

Las secciones 1 a 3 son los pasos: claridad temporal, tres cosas que no cuadraban y privacidad de las rutas.

## 1. Claridad temporal

- Cada gauge lleva debajo `ahora · hace N s`, calculado de `telemetria.at`: la hora de la medición
  misma, no la de `live.json`. Pasa a ámbar a los 60 s y a rojo («lectura vieja») a los 3 min. Los
  umbrales viven en `live.py` (`TELE_AMBER_S`, `TELE_RED_S`) y viajan en `live.json`
  (`tele_umbrales`), así que la página y los tests usan los mismos números.
- Cada curva dice su ventana y su paso: `últimos 1 h · 1 punto / 11 s`. El paso **se mide** con la
  mediana de los huecos entre puntos del anillo, no se escribe fijo. Medido: 11 s (el ciclo de
  10 s más lo que tarda la lectura).
- Junto al selector: «los gauges siempre son ahora; el selector solo cambia las curvas».
- Los procesos: `Procesos · top 12 · muestra de hace N s`.
- En la tarjeta de nodo hay dos tiempos con su contenido: `Instantánea (discos, memoria,
  montajes) hace 4 min` (la lenta, cada 5 min) y el chip `telemetría hace 9 s` (la rápida).

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## 2. Tres cosas que no cuadraban

### 2.1 La cabecera no sumaba los externos

Los renders del lab y los procesos adoptados los lista el planificador, que corre **después** de
armar la cabecera. `live.count_externos()` los suma al final del ciclo y la cabecera los desglosa:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

### 2.2 «Última señal hace —» en un externo

Un trabajo del lab no tiene latido propio. Su señal es la respuesta de `GET /jobs`, con la hora
del sondeo. El progreso no viene en `/jobs`: está en el log de cada render
(`logs/lab_jobs/<id>.log`), en una línea como `50/60 frames · … · ETA 42 s`. `collect.py` la lee
con `--lab-logs`; la carpeta está en `config/scheduler.toml`. Un proceso adoptado toma como señal
la lectura de procesos del nodo.

Render real lanzado para la prueba (`POST /jobs/<id>/restart` en el lab de la Mac):

```
4 min de render, 83 % (50/60 cuadros, faltan ~42 s)
última señal hace 3 s (GET /jobs)
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

El render era de 60 cuadros con una línea cada 50, así que solo hubo un dato de progreso, 40 s
antes del final. Un render de una sola imagen no escribe esa línea y muestra solo el tiempo.

### 2.3 Red y disco I/O en el PC; interfaz en la Mac

En el PC, `Get-Counter` y `typeperf` tardaron lo mismo (~1,7 s: la muestra es de 1 s). Se eligió
`typeperf`: no arranca PowerShell y da entrada y salida por separado.

```
PS> typeperf -sc 1 "\Network Interface(*)\Bytes Received/sec" "\Network Interface(*)\Bytes Sent/sec" "\PhysicalDisk(_Total)\Disk Bytes/sec"
"(PDH-CSV 4.0)","\\<equipo>\Network Interface(Intel[R] Ethernet Connection [2] I219-V)\Bytes Received/sec",...
"10/11/2026 07:12:31.415","74044.118943","0.000000","26028.697621","0.000000","524176.880953"
```

Corre en paralelo con la muestra de CPU. La lectura del PC pasa de ~0,25 s a ~1,35 s por ciclo de
10 s. Del CSV no se guarda el nombre del equipo.

En la Mac, `netstat -ibn` por interfaz da los deltas entre lecturas, y
`networksetup -listallhardwareports` dice qué es cada una (`en0` → Ethernet). La interfaz por la
que entraron más bytes da `entra por en0 (Ethernet)`, la forma de ver que el tráfico va por el
cable ([tutorial 29 §8](29-prioridades.md)). El disco sale de deltas de `iostat -Id`, que es
acumulado y no espera.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

**Fallo encontrado de paso:** `parse_netstat` leía columnas desplazadas en las interfaces sin
dirección (`gif0`, el `utun` de una VPN): tienen una columna menos. Con la VPN encendida, su
tráfico habría salido mal. Ya tiene test.

## 3. Privacidad: la causa sin el nombre de usuario

La tabla de Atención mostraba una causa con la ruta completa
`/Users/<usuario>/.enjambre/catalogo/backup-plus-manifest.tsv`. `razones.home_paths()` convierte
`/Users/<u>/` en `~/` y `C:\Users\<u>\` en `~\`, también con barras dobles de JSON. Se aplica en
cuatro sitios:

- al escribir una causa nueva (`_clip`);
- al mostrar las filas viejas del ledger (`build.alerts`), porque el ledger escrito no se
  reescribe;
- en `deid()` de la página, para la historia y los detalles;
- al publicar (`web.clean_why` / `clean_log`).

En la web, esa causa sigue saliendo como «exit 1 (detalle solo en local)»: `~/` aún cuenta como
ruta para el control de fugas.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## Cómo verificar (4. Comprobar)

```
PS> python -m pytest -q tests/test_visor_pulido.py
19 passed
```

Los fixtures del prompt: `telemetria.at` de hace 7 min → `crit`; un adoptado sin latido toma la
hora de la lectura de procesos (`lista de procesos`); la línea exacta del ledger con
`/Users/<u>/` sale con `~/`.

## 5. Lo que queda

- La pestaña Proyectos del visor **local** muestra como proyecto el nombre de la carpeta de inicio
  de la Mac (las sesiones abiertas en `~`). No es una causa (fuera de §3) y el nombre se guarda en
  `history.db`. Cambiar `project_of` partiría el historial en dos grupos. Propuesta: renombrarlo
  al mostrarlo. La copia web ya publica los proyectos solo por alias.
- En el detalle del PC, el gauge térmico dice «(sin sudo)», que es la razón de la Mac, no la del
  PC. Viene de antes; no se tocó.

## Qué salió mal de verdad

- La cabecera no sumaba los renders del lab ni los procesos adoptados (§2.1).
- Un externo del lab mostraba «Última señal hace —» porque no tiene latido propio (§2.2).
- `parse_netstat` leía columnas desplazadas en interfaces sin dirección (`gif0`, el `utun` de una VPN); ya tiene test (§2.3).
- La tabla de Atención mostraba el nombre de usuario en una ruta completa (§3).

## Autoevaluación

1. ¿Desde qué campo se calcula el «hace N s» de un gauge?
   Respuesta: de `telemetria.at`, la hora de la medición, no la de `live.json`.
2. ¿De dónde sale la señal y el progreso de un render del lab?
   Respuesta: la señal es la respuesta de `GET /jobs`; el progreso, de la línea del log `logs/lab_jobs/<id>.log`.
3. ¿Por qué se aplica `home_paths` también al mostrar las filas viejas del ledger?
   Respuesta: porque el ledger ya escrito no se reescribe.
