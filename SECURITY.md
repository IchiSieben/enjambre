# Security policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: open the repository's **Security** tab and choose
**Report a vulnerability** (security advisories). Do not open a public issue for anything that
could be exploited. Include the version or commit, what you did, what you expected and what
happened.

## Scope

In scope: the orchestrator (`enjambre.py` and its modules), `worker.sh`, the visor and its local
control endpoint, the bridge between nodes, and the publication audit.

Out of scope: vulnerabilities in Claude Code itself, macOS, Windows, tmux, OpenSSH or git (report
those upstream), and misconfigurations of your own machines.

## Design rules

These are the guarantees the project tries to keep. A violation of any of them is a valid report.

- No `--dangerously-skip-permissions`. Unattended nodes run with an explicit tool allow-list
  (`--allowedTools`); anything not listed is denied, not asked.
- No `sudo` on nodes. Steps that need it are written down for the owner to run.
- Sensitive-path rule: a repo whose path is marked confidential or reserved runs only on nodes you
  own, with the `claude` worker, never with an external engine. The check runs before any network
  call and the job is refused otherwise.
- Nodes never push. The orchestrator fetches each branch, scans exactly the unpushed commits with
  gitleaks and only then pushes.
- Secrets never live in the repo: no `.env`, tokens or keys in git, in the ledger or in the web
  copy of the visor. On macOS the keychain is reached through a tmux server started in the user's
  GUI session, so no token is written to disk.
- The visor's control endpoint listens on the loopback interface only and requires a token. The bridge between
  nodes is HMAC-signed and listens on the node's loopback interface.
- Published output is redacted: home paths are shortened before anything leaves the machine, and
  the web copy uses an allow-list.
- The publication audit (`tools/publicar/auditar.py`) runs in CI on every push and pull request,
  over the tree and the whole history: addresses, hardware identifiers, hostnames, home paths,
  emails, keys, forbidden words and unreviewed binaries. Any finding fails the run.
