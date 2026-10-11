# Arranque de zsh: .zshenv, .zprofile, .zshrc

## Qué es

zsh lee archivos distintos según cómo se abra:

| Archivo | Terminal de la Mac | `ssh mac "comando"` |
|---|---|---|
| `~/.zshenv` | sí | **sí** |
| `~/.zprofile` | sí (es login) | no |
| `~/.zshrc` | sí (es interactiva) | **no** |

`.zshenv` se lee siempre. `.zprofile` solo en shells de login. `.zshrc` solo en shells interactivas.

## Por qué importa aquí

`ssh mac "cmd"` abre un zsh **no interactivo y no-login**: solo lee `.zshenv`. Si el `PATH` está en `.zshrc`, todo funciona al abrir la Terminal de la Mac y todo falla por SSH con `command not found`. Para un agente que manda comandos a la Mac sin nadie mirando, eso es un fallo silencioso. La decisión y el tropiezo (`claude: command not found`, porque se instala en `~/.local/bin`) están en `../tutorial/02-homebrew-y-shell.md`.

## Ejemplo real

```
mac$ cat ~/.zshenv
export PATH="$HOME/.local/bin:$PATH"
eval "$(/opt/homebrew/bin/brew shellenv zsh)"
```

Prueba de que quedó bien, desde Windows:

```
PS> ssh mac 'command -v git jq tmux shellcheck gh'
/opt/homebrew/bin/git
/opt/homebrew/bin/jq
/opt/homebrew/bin/tmux
/opt/homebrew/bin/shellcheck
/opt/homebrew/bin/gh
```

## Error típico

Pegar en `.zprofile` o `.zshrc` las líneas que imprime el instalador de Homebrew ("Next steps"). Ahí sirven para tu terminal, no para los comandos remotos. Otro: poner en `.zshenv` algo que imprime texto o pide entrada; rompe `scp` y `rsync`, que esperan un canal limpio.

## Para saber más

- Manual de zsh, "Startup/Shutdown Files": https://zsh.sourceforge.io/Doc/Release/Files.html
