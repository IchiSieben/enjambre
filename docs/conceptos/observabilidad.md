# Observabilidad

**Qué es:** la capacidad de saber qué está pasando dentro de un sistema **mirando lo que ya
emite**: registros (logs), métricas y trazas. No hay que entrar a cada máquina a preguntar. Un
sistema es observable si puedes responder una pregunta nueva ("¿por qué ayer gastó el doble?")
con los datos que ya guarda, sin desplegar código nuevo.

Los tres pilares clásicos:

| Pilar | Qué es | En el enjambre |
|---|---|---|
| **Registros** | eventos con fecha, uno por línea | `jobs.jsonl` (un trabajo = una línea), `watch.log`, `build.log`, los transcripts de Claude Code |
| **Métricas** | números agregados en el tiempo | tokens por día, % de cuota, GB libres, % de memoria |
| **Trazas** | el recorrido de una petición por varias piezas | `claude -p` en la Mac → rama → `bring_branch_home` → ledger: hoy se reconstruye a mano con el id del trabajo |

**Por qué importa aquí:** con dos máquinas, "¿está todo bien?" eran cinco comandos. Con cuatro
nodos serían veinte. El [visor maestro](../tutorial/20-visor-maestro.md) no mide nada nuevo:
**junta** lo que cada pieza ya escribía y lo pone en una pantalla y en una línea de la barra.

**Ejemplo real:** la pregunta "¿cuánto llevo consumido hoy entre las dos máquinas?" no tenía
respuesta en ningún archivo. Los datos sí estaban: cada transcript guarda el `usage` de cada
respuesta. Faltaba sumarlo bien (una vez por `message.id`, incluyendo subagentes, por día de Lima)
y en los dos nodos. La respuesta salió de **agregar**, no de instrumentar.

**Error típico: inventar la métrica que falta.** Los transcripts no traen coste en dólares. La
tentación es multiplicar por un precio recordado y mostrar "$43,17" con dos decimales, que
parece exacto. El visor muestra "≈ equivalente API" y dice qué parte quedó sin precio. Un número
falso con buena tipografía es peor que un "no medido", porque se toman decisiones con él.

**Segundo error típico: el dato viejo que parece actual.** Una instantánea de hace una hora
mostrada como si fuera de ahora. Todo dato del visor lleva su edad ("hace 3 min"). Pasado su
umbral se marca: "VIEJA" en la página, `⌛20m` en la barra.

**Relación con otras tarjetas:** [sondeo vs latido](sondeo-vs-latido.md) es *cómo* llegan los
datos (aquí Windows pregunta por SSH); [mínimo privilegio](minimo-privilegio-para-agentes.md) es
*qué no debe salir* (el visor nunca lee prompts ni `*.key`).

**Para leer más:** Charity Majors, Liz Fong-Jones y George Miranda, *Observability Engineering*
(O'Reilly, 2022), capítulos 1–2; Google, *Site Reliability Engineering*, capítulo 6
("Monitoring Distributed Systems"), donde aparecen las "cuatro señales doradas": latencia,
tráfico, errores y saturación.
