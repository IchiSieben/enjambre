# Tarjetas de concepto

Una página corta por idea que apareció de verdad en este proyecto. Cada tarjeta tiene: qué es, por qué importa aquí, ejemplo real, error típico y dónde leer más. Los ejemplos salen de las fuentes del repo; donde las fuentes no conservan la salida literal, la tarjeta lo dice.

- [Huella de host SSH](huella-de-host-ssh.md): la pregunta `yes/no` de la primera conexión y `known_hosts`.
- [Huella TLS](huella-tls.md): el diálogo de huellas de Deskflow y el aviso "does not match".
- [Arranque de zsh](arranque-de-zsh.md): por qué `ssh mac "cmd"` solo lee `.zshenv`.
- [DHCP y reservas](dhcp-y-reservas.md): IP prestada, MAC privada "Fixed" y la IP que cambió.
- [Git como bus](git-como-bus.md): ramas en vez de carpeta compartida.
- [Worktrees](worktrees.md): una carpeta y una rama por trabajo.
- [Mínimo privilegio para agentes](minimo-privilegio-para-agentes.md): `--allowedTools`, sin sudo, NEEDS-OWNER y el llavero.
- [Idempotencia](idempotencia.md): correr dos veces = 0 cambios.
- [Regla 3-2-1](regla-3-2-1.md): copias, medios, fuera de sitio y verificación (el paso de respaldo aún no se ejecutó).
- [Sondeo vs latido](sondeo-vs-latido.md): quién pregunta a quién para saber si un nodo está vivo, y por qué aquí pregunta Windows.
- [Cola de trabajos](cola-de-trabajos.md): pedir y ejecutar desacoplados; las reglas que hacen confiable una cola de carpetas.
- [Observabilidad](observabilidad.md): registros, métricas y trazas; juntar lo que ya existe y no inventar la métrica que falta.
- [Enrutamiento](enrutamiento.md): una tabla más el estado vivo eligen la máquina; filtros duros, historia solo con dos nodos medidos, y el punto ciego de la GPU.
- [Consola heredada en Windows](consola-heredada-en-windows.md): por qué `pythonw` hace parpadear ventanas de `ssh.exe`, `CREATE_NO_WINDOW` + `SW_HIDE`, y cómo probar que ya no pasa.
- [Política y mecanismo](politica-y-mecanismo.md): la regla que se lee (`rules/enjambre.md`, `routing.toml`) y el código que la hace cumplir (el hook, `routing.py`).
- [Pull vs push](pull-vs-push.md): por qué la PC pregunta a la bandeja cada 2 min en vez de que el hosting llame a casa: sin puertos de entrada, a costa de latencia.
- [Permitidos vs prohibidos](permitidos-vs-prohibidos.md): allowlist como primer muro (`web.public_snapshot`) y denylist solo como segundo (`leak_check`), con el falso positivo del logo.
- [Latido y reanudación](latido-y-reanudacion.md): un supervisor que escribe "sigo vivo y voy en X" y un trabajo que, si muere, sigue donde quedó; vivo se comprueba por pid, no se cree.
- [Presupuesto por trabajo](presupuesto-por-trabajo.md): tope en dólares por trabajo (`--max-budget-usd`), estimación en el latido sin contar doble, y lo que queda al reanudar.
- [Una vez vs sesión persistente](una-vez-vs-sesion-persistente.md): `claude -p` nace y muere; la sesión persiste como transcripción en disco (`session_id` + `resume`), no como proceso vivo.
- [Backplane](backplane.md): el servicio intermedio por el que pasan los mensajes entre participantes; por qué el puente no usa uno y la bandeja sí, mínimo.
