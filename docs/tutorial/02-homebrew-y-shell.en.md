---
lesson:
  route: build-stack
  order: 3
  level: 1
lang: en
---

# 02 — Homebrew, Claude Code and the Mac shell

## Goal

The Mac with Homebrew, Claude Code, `gh` and the base tools, all visible to commands that arrive
over SSH too (`ssh mac 'git --version'` answers, it does not say `command not found`).

## Why

Homebrew is the de facto package manager on macOS. The base tools (`jq`, `tmux`,
`coreutils`, a modern `bash`, `shellcheck`) are the ones used by the scripts that dispatch work:
if one is missing, the task fails halfway through with nobody watching.

And one concept decides whether everything works over SSH: **zsh reads different files depending on
how it is opened**. A remote command (`ssh mac "cmd"`) opens a *non-interactive, non-login* zsh, which only reads
`~/.zshenv`:

| File | Mac terminal | `ssh mac "command"` |
|---|---|---|
| `~/.zshenv` | yes | **yes** |
| `~/.zprofile` | yes (it is a login shell) | no |
| `~/.zshrc` | yes (it is interactive) | **no** |

If the PATH lives in `.zshrc`, everything works in the Mac's Terminal and everything fails over SSH.

## Steps

### 1. Homebrew (by hand: it asks for your password)

```
mac$ /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

The official installer installs the Xcode Command Line Tools on its own if they are missing, and asks for the
password (sudo). When it finishes it prints some "Next steps" lines: do not paste them yet, read
step 3 first.

### 2. Claude Code and GitHub CLI

```
mac$ curl -fsSL https://claude.ai/install.sh | bash
```
```
mac$ brew install gh
```

The `gh` login is done with a browser, in the Mac's Terminal (not over SSH):

```
mac$ gh auth login -h github.com
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

The screenshot shows HTTPS, *Login with a web browser*, a one-time code (covered) and
`Logged in as …`. The last thing it prints is *"You were already logged in to this account"*:
there was a previous login. See "What actually went wrong" for why this matters.

### 3. The PATH goes in `~/.zshenv`

```
mac$ cat ~/.zshenv
export PATH="$HOME/.local/bin:$PATH"
eval "$(/opt/homebrew/bin/brew shellenv zsh)"
```

Test, from Windows, that it came out right:

```
PS> ssh mac 'command -v git jq tmux shellcheck gh'
/opt/homebrew/bin/git
/opt/homebrew/bin/jq
/opt/homebrew/bin/tmux
/opt/homebrew/bin/shellcheck
/opt/homebrew/bin/gh
```

### 4. Base tools (idempotent: check first)

```powershell
PS> ssh mac 'brew list --versions git jq bash coreutils tmux shellcheck gh'
```
```powershell
PS> ssh mac 'brew list --cask --versions visual-studio-code ghostty deskflow'
```

If any is missing: `brew install git jq bash coreutils tmux shellcheck` and
`brew install --cask visual-studio-code ghostty`. On this Mac they were all already there:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

### 5. Symmetric folders

```powershell
PS> ssh mac 'mkdir -p ~/Documents/Portfolio'
```

The Mac uses the same structure as Windows (`Documents/Portfolio/<repo>`), because several
tools resolve paths relative to that folder.

## What actually went wrong

1. **`claude: command not found`** right after installing Claude Code. The installer puts it
   in `~/.local/bin`, which was not in the `PATH`. Fix: PATH and `brew shellenv` in
   `~/.zshenv`, not in `.zshrc` (see "Why").
2. **`brew install deskflow` → "Refusing to load cask from untrusted tap".** Homebrew requires
   explicitly trusting third-party taps: `brew trust deskflow/tap` and then install.
3. **`brew doctor`: "Your Command Line Tools are too outdated".** Fixed from Settings →
   General → Software Update (it installs the new CLT version). It came back after
   updating macOS: see `06-actualizar-macos.md`.
4. **`gh auth status` over SSH: "The token in default is invalid".** Session 1 diagnosed it
   as "the login expired". Later it looked like it was probably something else: the token was fine,
   but stored in the macOS **keychain**, which an SSH session cannot open. Evidence: screenshot 07
   shows a successful `gh auth login` on the Mac before that session, and later
   `gh auth login` answered "you were already logged in". That the earlier login is the cause of the
   "invalid" is an inference; what is measured (in the overnight run) is the keychain
   lock over SSH. Details and options in `09-agente-desatendido.md`.

## How to verify

```
PS> ssh mac 'command -v git jq tmux shellcheck gh'
```

It must print five paths under `/opt/homebrew/bin/`, and

```
PS> ssh mac 'claude --version'
2.1.289 (Claude Code)
```

(Versions as of 2026-10-04: Homebrew 7.0.7, Claude Code 2.1.289.)

## Self-check

1. Why does the PATH go in `~/.zshenv` and not in `~/.zshrc`?
   <details><summary>Answer</summary>`ssh mac "cmd"` opens a non-interactive, non-login zsh, which
   only reads `~/.zshenv`.</details>
2. What does "idempotent" mean in step 4?
   <details><summary>Answer</summary>You check before installing and, if it is already there, leave it alone:
   running it twice gives the same result as running it once.</details>
3. `gh auth login` works in the Mac's Terminal but fails over SSH. Where do you look?
   <details><summary>Answer</summary>At where the token was stored: the macOS keychain does not
   open from an SSH session.</details>
