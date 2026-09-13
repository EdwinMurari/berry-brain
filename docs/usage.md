# Setup and data

[Home](../README.md) · [Learning](learning.md) · [Development](development.md)

## Install and connect

From a downloaded or cloned source directory, install into a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/berry-brain-configure codex --local
.venv/bin/berry-brain-configure claude --local
```

On Debian or Ubuntu, install the matching `python3-venv` package if the first
command reports that `ensurepip` is missing.

On Windows, use `py -3 -m venv .venv`, then use `.venv\Scripts\python.exe`
and `.venv\Scripts\berry-brain-configure.exe` for the same steps.
Install the clients first. The Codex CLI must be available on your PATH.
Keep the virtual environment at this location: registration saves its absolute path.
After source updates, run the install and configure commands again. Reopen client sessions to load
new code. Existing data stays in its separate data directory.

Setup registers the tools and adds a short brain reminder to the client's global
instructions. It updates only the marked Berry Brain section.
Other text stays intact. Rerunning setup updates the section without adding a
duplicate.
Codex registration uses its CLI. Claude registration updates only the
`berry-brain` entry in its user JSON settings, so repeat setup also works.
The reminder text has one source in [`configure.py`](../src/berry_brain/configure.py); detailed learning
rules stay in the MCP tool descriptions.

Codex uses `AGENTS.md` in `CODEX_HOME` (normally `~/.codex`), or its nonempty
`AGENTS.override.md` when present. Claude uses `CLAUDE.md` in `CLAUDE_CONFIG_DIR`
(normally `~/.claude`). Existing symlinks are preserved. If Claude's file contains
only one `@file` import, setup updates that existing file and preserves the wrapper.
For more complex import layouts, keep the reminder in one chosen shared file.
The installer does not parse a full Markdown import tree.
Malformed reminder sections stop setup before it changes a registration.

Both clients use the same local database and the project `default`. Start a new
session after registration. Ask the agent to check its `brain_*` tools, recall a
task, and save a small checked outcome. Installation makes the tools available;
it does not force a client to use them on every task.

For separate project scopes, register each client with the same project list:

```sh
.venv/bin/berry-brain-configure codex --local --project app-one --project app-two
.venv/bin/berry-brain-configure claude --local --project app-one --project app-two
```

This replaces that client's `berry-brain` registration. Include all projects it
should use. Removing a project from the list does not delete its saved data.
For other clients, register `.venv/bin/berry-brain --local --identity my-client`
as a stdio MCP server. Add the same `--project` options as needed.

An existing server user should keep their current `--config` registration. Local
mode creates separate storage; it does not import or replace a server database.
The hosting application owns its authentication and deployment settings.

## Data and privacy

Default database locations:

| System | Location |
| --- | --- |
| Linux | `$XDG_DATA_HOME/berry-brain/brain.sqlite3`, or `~/.local/share/berry-brain/brain.sqlite3` |
| macOS | `~/Library/Application Support/berry-brain/brain.sqlite3` |
| Windows | `%LOCALAPPDATA%\berry-brain\brain.sqlite3` |

Use `--data-dir /absolute/private/directory` on both client registrations to choose
another location. Keep it outside the source tree and on a local disk. Do not
synchronize the live SQLite database through file-sync services or network shares.
For access from several machines, run the MCP process on one host through SSH,
or use an application that hosts the brain API. Keep the database on that host.

Local mode makes no network requests and has no telemetry. Recalled text goes to
the connected AI client and can reach its model provider under that client's data
policy. Records are not encrypted by this package. Use your OS disk encryption
and account permissions. On Linux and macOS, the data directory must have mode
`0700` and its database files mode `0600`. Windows uses the user's folder ACLs.

Project scopes limit tool access; local mode trusts the OS account. Any process
that can read the database can read its content. Use an authenticated hosting application for
clients that need separate credentials. Never save secrets, personal facts, raw
transcripts, or hidden reasoning. The tool instructions prohibit these; there is
no automatic filter that can guarantee their removal.

For backup, close connected clients and copy the whole data directory. Restore
it only while clients are closed. Software updates do not delete it.
