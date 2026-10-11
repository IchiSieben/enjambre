# Estudio: qué enseña enjambre

Cinco conceptos que este proyecto ejercita de verdad. Cada ficha sigue el formato de
[`docs/conceptos/`](conceptos/README.md): qué es, por qué importa aquí, ejemplo real, error típico.
Al final de cada una, las historias de la Academia de iC7 que cuentan esa pieza y los archivos
locales donde está el detalle.

Base de la Academia: https://ichisieben.dev (páginas en español bajo `/es/`).

---

## 1. Git como bus

### Qué es

Un bus es el canal por donde viajan los resultados entre las piezas de un sistema. Aquí el canal
es git: cada trabajo termina en una rama y el resultado se mueve con `push` y `fetch`. La
alternativa tentadora es una carpeta compartida donde todos leen y escriben.

### Por qué importa aquí

Con una carpeta compartida, dos procesos pueden escribir el mismo archivo a la vez y nadie lo nota
hasta que uno queda a medias. Con git, el resultado llega completo (un commit) o no llega, y cada
trabajo tiene su rama. Cuando un nodo no pudo hacer push, el bus siguió funcionando: el
orquestador trajo la rama por SSH con `git fetch`, la escaneó con gitleaks y la subió. Cambió quién
da el último salto, no el modelo.

### Ejemplo real

```
git fetch <nodo>:<ruta-del-repo> mac/<id>:mac/<id>
```

Cada trabajo es una rama `mac/<id>`; fusionar a `main` es decisión humana, nunca del agente.

### Error típico

Usar git pero con una sola rama para todos los trabajos: la carrera reaparece, solo que en el
`merge`.

### Historias

- Blog: [La primera tarea distribuida](https://ichisieben.dev/es/academy/blog/la-primera-tarea-distribuida/)
- Concepto: [Git como bus](https://ichisieben.dev/es/academy/concepts/git-como-bus/) ·
  [Worktrees](https://ichisieben.dev/es/academy/concepts/worktrees/) ·
  [Idempotencia](https://ichisieben.dev/es/academy/concepts/idempotencia/)
- Local: [git-como-bus](conceptos/git-como-bus.md) · [worktrees](conceptos/worktrees.md) ·
  [idempotencia](conceptos/idempotencia.md) ·
  [tutorial 08](tutorial/08-primera-tarea-distribuida.md)

---

## 2. Enrutamiento por tabla

### Qué es

Decidir en qué máquina corre un trabajo. Aquí lo decide una tabla más el estado vivo, no un modelo:
`routing.toml` dice qué necesita cada tipo de trabajo y qué máquina se prefiere, `nodes.toml` dice
qué tiene cada máquina y `state/` dice quién está despierto y ocupado.

### Por qué importa aquí

Elegir nodo es una decisión repetible: con las mismas entradas debe dar lo mismo y debes poder leer
por qué. Una tabla se corrige con un editor de texto y se prueba con tests. Un modelo cuesta cuota
en cada trabajo y no sabe si la Mac está apagada. El orden es fijo: filtros duros (capacidades,
memoria, regla de rutas reservadas, nodo listo), estado vivo (huecos libres, presión de memoria),
historia medida (solo si al menos dos nodos libres tienen cinco corridas buenas de ese tipo) y, al
final, el primero de la lista de preferencia.

### Ejemplo real

`python enjambre.py run --kind tests --repo <repo> --task tasks/TASK-<id>.md --dry-run` imprime la
ruta elegida y el porqué en una línea, y la decisión queda en el ledger (`routed_by`).

### Error típico

Dejar que la "historia" decida con una sola muestra: un nodo con una corrida afortunada gana para
siempre. Por eso el umbral son dos nodos con muestras suficientes.

### Historias

- Blog: [Una tabla elige la máquina](https://ichisieben.dev/es/academy/blog/una-tabla-elige-la-maquina/)
- Concepto: [Enrutamiento](https://ichisieben.dev/es/academy/concepts/enrutamiento/) ·
  [Cola de trabajos](https://ichisieben.dev/es/academy/concepts/cola-de-trabajos/) ·
  [Política y mecanismo](https://ichisieben.dev/es/academy/concepts/politica-y-mecanismo/)
- Local: [enrutamiento](conceptos/enrutamiento.md) · [cola-de-trabajos](conceptos/cola-de-trabajos.md) ·
  [politica-y-mecanismo](conceptos/politica-y-mecanismo.md) · [tutorial 22](tutorial/22-enrutador.md)

---

## 3. Mínimo privilegio para agentes

### Qué es

Dar a un agente solo lo que necesita para la tarea, y que lo no permitido se niegue en vez de
preguntarse. Con nadie mirando, una pregunta de permiso es un bloqueo; un permiso de más es un
riesgo.

### Por qué importa aquí

Cuatro decisiones del proyecto: lista explícita de herramientas (`--allowedTools`) y nunca
`--dangerously-skip-permissions` en un nodo sin supervisión; sin `sudo` en los nodos; los
secretos nunca en el repo; y una regla de rutas reservadas que limita los repos confidenciales a
nodos propios. Una cuarta capa es el contrato: cada tarea declara qué archivos puede tocar y qué
cuenta como hecho.

### Ejemplo real

Un trabajo real intentó editar una ruta protegida de la configuración del agente. La protección
de rutas sensibles de Claude Code lo negó, y `--allowedTools` no la anula. El agente no intentó
rodearla y dejó el parche en su mensaje final; se aplicó a mano en la revisión. El ledger decía
`exit 0`: los códigos de salida no verifican los criterios de hecho.

### Error típico

Confundir "el agente no preguntó" con "el agente terminó bien". Sin lista de permitidos explícita,
lo desatendido se vuelve un `--dangerously-skip-permissions` encubierto.

### Historias

- Blog: [Una noche con un agente desatendido](https://ichisieben.dev/es/academy/blog/una-noche-con-un-agente-desatendido/) ·
  [El kit en la Mac](https://ichisieben.dev/es/academy/blog/el-kit-en-la-mac/)
- Concepto: [Mínimo privilegio para agentes](https://ichisieben.dev/es/academy/concepts/minimo-privilegio-para-agentes/) ·
  [Permitidos vs prohibidos](https://ichisieben.dev/es/academy/concepts/permitidos-vs-prohibidos/) ·
  [Presupuesto por trabajo](https://ichisieben.dev/es/academy/concepts/presupuesto-por-trabajo/)
- Local: [minimo-privilegio-para-agentes](conceptos/minimo-privilegio-para-agentes.md) ·
  [permitidos-vs-prohibidos](conceptos/permitidos-vs-prohibidos.md) ·
  [presupuesto-por-trabajo](conceptos/presupuesto-por-trabajo.md) ·
  [tutorial 09](tutorial/09-agente-desatendido.md) · [tutorial 07](tutorial/07-orquestador.md)

---

## 4. Sondeo, latido y reanudación

### Qué es

Dos formas de saber si algo está vivo. En el sondeo, quien necesita saberlo pregunta. En el latido,
quien trabaja avisa cada cierto tiempo. Un trabajo largo que nadie mira necesita además poder
reanudarse si muere a mitad.

### Por qué importa aquí

Aquí pregunta la PC: el nodo no necesita saber dónde está el orquestador, y una caída del nodo se
ve como silencio, no como un aviso que nunca llega. Los trabajos largos (`enjambre script` y
`run --detach`) vuelven al instante, laten, se reanudan si mueren y dejan una línea en el ledger.
El caso instructivo: los avisos decían que la Mac estaba caída, pero era la VPN; y el pánico del
kernel no lo causaron los modelos sino el vigilante que debía evitarlo.

### Ejemplo real

`enjambre watch` mantiene la presencia de cada nodo (caído, ping, ssh, listo) con antirrebote, y
los trabajos dirigidos a un nodo caído esperan en una cola de carpetas hasta que vuelva.

### Error típico

Un vigilante que actúa con una sola lectura mala: mide dos veces o no actúes. Y un vigilante con
permiso de matar procesos es un proceso más que puede matar el equivocado.

### Historias

- Blog: [El vigilante que mataba al equivocado](https://ichisieben.dev/es/academy/blog/el-vigilante-que-mataba-al-equivocado/) ·
  [La IP que cambió](https://ichisieben.dev/es/academy/blog/la-ip-que-cambio/)
- Concepto: [Sondeo vs latido](https://ichisieben.dev/es/academy/concepts/sondeo-vs-latido/) ·
  [Latido y reanudación](https://ichisieben.dev/es/academy/concepts/latido-y-reanudacion/) ·
  [Pull vs push](https://ichisieben.dev/es/academy/concepts/pull-vs-push/) ·
  [Una vez vs sesión persistente](https://ichisieben.dev/es/academy/concepts/una-vez-vs-sesion-persistente/)
- Local: [sondeo-vs-latido](conceptos/sondeo-vs-latido.md) ·
  [latido-y-reanudacion](conceptos/latido-y-reanudacion.md) · [pull-vs-push](conceptos/pull-vs-push.md) ·
  [una-vez-vs-sesion-persistente](conceptos/una-vez-vs-sesion-persistente.md) ·
  [tutorial 15](tutorial/15-presencia-y-cola.md) · [tutorial 25](tutorial/25-trabajos-largos.md)

---

## 5. Observabilidad sin inventar métricas

### Qué es

Saber qué pasa dentro de un sistema mirando lo que ya emite: registros, métricas y trazas. Un
sistema es observable si puedes responder una pregunta nueva ("¿por qué ayer gastó el doble?") con
los datos que ya guarda, sin desplegar código nuevo.

### Por qué importa aquí

El visor no inventa un sistema de métricas: junta lo que ya existe. El ledger (registros), una
lectura de telemetría por nodo cada 10 s (métricas: CPU, RAM con presión y swap, temperatura, red,
procesos etiquetados con su trabajo) y las transcripciones de Claude Code (consumo, en equivalente
API y en cuota). Cada fallo se muestra con su causa. Y hay un modo demo que pasa por el mismo código
con datos sintéticos, para poder enseñar el visor sin enseñar ninguna máquina real.

### Ejemplo real

`python enjambre.py visor --demo --serve` regenera lecturas sintéticas cada 10 s para grabar un
video del visor sin tocar un nodo real.

### Error típico

Medir una sola cosa y llamarlo "monitoreo": la temperatura sin el trabajo que corría no explica
nada. Etiqueta cada proceso con su trabajo o sesión.

### Historias

- Blog: [¿Qué rinde una Mac mini de 16 GB?](https://ichisieben.dev/es/academy/blog/que-rinde-una-mac-mini-16gb/) ·
  [El puente](https://ichisieben.dev/es/academy/blog/el-puente/)
- Concepto: [Observabilidad](https://ichisieben.dev/es/academy/concepts/observabilidad/) ·
  [Backplane](https://ichisieben.dev/es/academy/concepts/backplane/)
- Local: [observabilidad](conceptos/observabilidad.md) · [backplane](conceptos/backplane.md) ·
  [tutorial 20](tutorial/20-visor-maestro.md) · [tutorial 28](tutorial/28-telemetria.md) ·
  [tutorial 26](tutorial/26-puente.md) · [tutorial 18](tutorial/18-mac-vs-windows.md)
