# Sondeo vs latido

**Qué es.** Hay dos formas de saber si una máquina está viva:
- **Sondeo** (*polling*): el que quiere saber pregunta cada cierto tiempo, y quien responde está
  vivo.
- **Latido** (*heartbeat*): cada máquina avisa sola cada cierto tiempo, y si el aviso no
  llega, se la da por caída.

| | Sondeo (Windows pregunta) | Latido (el nodo avisa) |
|---|---|---|
| Quién necesita un puerto abierto | El **nodo** (SSH, que ya está abierto) | El **orquestador**: un receptor nuevo en Windows |
| Qué mide | Que el orquestador **llega** al nodo, por el mismo camino que usan los trabajos | Que el nodo **llega** al orquestador, que es otro camino |
| Un nodo nuevo | Basta con agregarlo a `nodes.toml` | Hay que instalarle el emisor y darle la dirección del orquestador |
| Coste | Una conexión SSH por nodo cada 30 s | Un paquete por nodo cada 30 s |
| Falla silenciosa | Si el sondeador muere, nadie pregunta (se ve en la antigüedad del `nodes.json`) | Si el receptor muere, todos los nodos "se caen" a la vez |
| Detecta | Ping, SSH y la sesión `tmux` con llavero: lo que de verdad hace falta para correr un trabajo | Que el proceso emisor corre, no que SSH o el llavero funcionen |

## Qué elegimos y por qué

**Sondeo en las dos direcciones.**
- **Windows → nodos**: `enjambre watch` pregunta cada 30 s. La razón principal es que mide
  exactamente el camino que usa `enjambre run` (ping → SSH → `tmux -L enjambre`). Un latido
  diría "el nodo está encendido" aunque su llavero estuviera cerrado.
- **Mac → Windows**: un LaunchAgent prueba cada 60 s una conexión TCP al puerto de Deskflow
  (24800), el único servicio de Windows que la Mac ya alcanza. Windows no tiene servidor SSH.
  Un latido hacia Windows exigiría abrir un puerto nuevo, y con el perfil de red *Público* eso
  significa crear una regla de firewall, que necesita permisos de administrador.

**Descartado:** el latido. Habría que mantener un receptor en Windows y abrir un puerto nuevo,
y lo que mediría no es lo que importa para despachar trabajos. Si algún día hay muchos nodos,
o nodos detrás de NAT a los que Windows no puede llegar, el latido vuelve a tener sentido: con
nodos que no se pueden alcanzar, la única forma de saber de ellos es que avisen.

## Dos detalles que muerden

- **Antirrebote.** Una sola respuesta perdida no tumba un nodo. Hacen falta dos fallos
  seguidos para marcarlo "caído". En cambio, subir de nivel es inmediato.
- **En Windows, `ping` devuelve 0 aunque diga *Destination host unreachable*.** El nivel
  "responde a ping" se decide buscando `TTL=` en la salida, no por el código de salida.

Lo aplican `enjambre.py` (`watch`) y `node-watch.sh`; ver `../tutorial/15-presencia-y-cola.md`.
