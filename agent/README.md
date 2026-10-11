# puente — a persistent Claude Code on another machine, reachable over SSH

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Claude Agent SDK](https://img.shields.io/badge/Claude_Agent_SDK-0.2.164-D97757)
![macOS](https://img.shields.io/badge/macOS-launchd-000000?logo=apple&logoColor=white)
![stdlib](https://img.shields.io/badge/server-stdlib_http-informational)

Talk from one machine to a Claude Code session that **stays alive** on another one: same
conversation across messages, dropped connections and service restarts, with the cost of every
turn recorded. Part of [enjambre](../README.md); Spanish walkthrough in
[docs/tutorial/26-puente.md](../docs/tutorial/26-puente.md), design and evaluation in
[docs/PUENTE.md](../docs/PUENTE.md).

```
client ──ssh── curl ──▶ 127.0.0.1:8766  puente.py ──▶ Agent SDK ──▶ claude (+ MCP: Playwright)
                         HMAC-signed        │ queue: one turn at a time per repo
                                            └▶ turns/<id>.ndjson (events)  sessions/<repo>.json (session_id, cost)
```

## Why it is built this way

- **The turn does not live inside the HTTP request.** It runs in the server's own loop and
  writes events to a file; a client reads from any offset. Closing the client stops reading,
  never the work.
- **No network port.** Loopback only; the client reaches it through `ssh host curl`. ssh is the
  authentication and the encryption; every request is *also* HMAC-signed (timestamp, nonce,
  method, path, body hash; ±120 s, single use) and any request with an `Origin` header is refused,
  so neither a local process nor a web page can drive it without the key.
- **A session is a file, not a process.** `session_id` is on disk and the SDK's `resume` reloads
  the transcript; after a restart the running turn becomes `interrupted` and the next one resumes.
- **Allowlist, never a prompt.** Nobody watches the remote machine: tools outside the list are
  denied by `can_use_tool`, recorded, and the turn ends `incomplete`.

## API (all signed)

| Method | Path | |
|---|---|---|
| `POST` | `/turns` | `{"repo", "message", "allowed": [...], "model"?, "max_turns"?, "budget_usd"?}` → `202 {"turn", "position", "session_id"}` |
| `GET` | `/turns/<id>` | turn meta: state (`queued/running/ok/failed/incomplete/interrupted`), cost, result, denials |
| `GET` | `/turns/<id>/events?from=N&follow=1` | NDJSON stream (`queued`, `start`, `text`, `tool`, `denied`, `error`, `end`, keepalive `beat`) |
| `GET` | `/sessions` | one per repo: `session_id`, turns, accumulated `cost_usd`, running/queued |
| `GET` | `/health` | engine, pid, console user (GUI session or login window) |

## Run it

```sh
bash install.sh            # on the server machine: venv (uv, Python 3.12), key (600), LaunchAgent
bash install.sh stub       # echo engine: same events, no model, no cost — wiring checks
python -m unittest tests.test_puente   # 12 tests, stub engine, no network beyond 127.0.0.1
```

Signing (both sides): `hex(HMAC-SHA256(key, f"{ts}\n{nonce}\n{METHOD}\n{path}\n{sha256(body)}"))`
in `X-Puente-Ts`, `X-Puente-Nonce`, `X-Puente-Sig`.

## Threat model, in one line

Whoever holds the key (any process of your user on either machine) can spend your Claude account
up to the per-turn budget, on repos that have an `AGENTS.md`, with the tools that file allows —
nothing outside `~/Documents/Portfolio`, no `git push`, no Edit/Write unless the client passes
`--edit`. Playwright is allowed by tool **name** (19 tools), never as a whole server: it also
ships `browser_run_code_unsafe` (RCE-equivalent), `browser_evaluate`, `browser_file_upload` and
`browser_drop`. What remains is prompt injection from a visited page steering the allowed tools.
Full table in the tutorial, §5.

License: not chosen yet (the enjambre repo is private; pick one before publishing this piece).
