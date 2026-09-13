# Berry Brain

Private project memory for AI clients. Save checked outcomes, test useful lessons,
and resume work across Codex, Claude Code, or another stdio MCP client.

Each installation starts empty. Local use needs no account, model key, hosted
service, or Berry infrastructure. It needs Python 3.11 or later with SQLite FTS5.
Standard Python distributions include FTS5.

## Start

Install from a downloaded or cloned copy of this repository:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/berry-brain-configure codex --local
.venv/bin/berry-brain-configure claude --local
```

Install the clients first. The Codex CLI must be on your PATH.
On Debian or Ubuntu, install the matching `python3-venv` package if needed.
On Windows, use `py -3 -m venv .venv` and the commands under `.venv\Scripts\`.
See [setup and data](docs/usage.md) for full steps.

Setup adds the tools and a short global reminder. Both clients use the same
private database. Reopen their sessions, then ask an agent to recall a task and
save a checked outcome. Keep the virtual environment at its installed location.
After updates, rerun installation and setup. Saved data stays outside this repo.

## How it helps

Agents save outcomes and propose lessons. A lesson needs helpful reports from
two later tasks with distinct evidence before normal recall can return it.
Harmful feedback retires it. Recall has a size limit and uses only relevant matches.

This changes the context given to the model. It does not train model weights or
guarantee better results. Stored text is evidence, never instructions or permission.
See [learning and skills](docs/learning.md) for the rules and limits.

## Browse the project

```text
src/berry_brain/  One engine, its adapters, and client setup
tests/           Offline checks with synthetic data
docs/            Setup, learning rules, and development
.github/         Automated test workflow
pyproject.toml   Package metadata and command entry points
```

- [Setup, client scopes, privacy and backup](docs/usage.md)
- [Learning, evidence and skills](docs/learning.md)
- [Source map, tests, host integration and upgrades](docs/development.md)

Local mode makes no network requests. Recalled text can reach the connected
client's model provider. Keep private data, credentials and backups out of Git.
The public package contains no hosting stack or homelab settings.

Released under the [MIT license](LICENSE).
