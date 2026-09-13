# Setup and data

[Home](../README.md) · [Learning](learning.md) · [Development](development.md)

## Install

You need Python 3.11 or later with SQLite FTS5 support. Standard Python builds
include FTS5. Install your AI clients first. The Codex command must be on your
`PATH`.

Download or clone this repository. Open a terminal in its directory.

### Linux and macOS

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/berry-brain-configure codex --local
.venv/bin/berry-brain-configure claude --local
```

On Debian or Ubuntu, a missing `ensurepip` error can mean that `python3-venv` is
not installed. Install the package that matches your Python version, then retry.

### Windows

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\berry-brain-configure.exe codex --local
.venv\Scripts\berry-brain-configure.exe claude --local
```

Run the setup command for each client you use. Reopen its sessions after setup.
Ask the agent to check its `brain_*` tools, recall a task, and save a checked result.

Both clients use the same database and the project `default`. Setup makes the
tools available. It does not force the client to use memory on each task.

Keep the virtual environment at this path. Setup saves its full path.
After source updates, repeat the install and setup commands. Then reopen client
sessions. The saved data stays in its separate directory.

## Global instructions

Setup adds a short brain reminder to the client's global instructions.
It changes only the marked Berry Brain section. Other text stays intact.
Repeated setup updates the same section.

| Client | Instruction file |
| --- | --- |
| Codex | `AGENTS.md` in `CODEX_HOME`, normally `~/.codex` |
| Claude Code | `CLAUDE.md` in `CLAUDE_CONFIG_DIR`, normally `~/.claude` |

For Codex, setup uses a nonempty `AGENTS.override.md` if one exists.
It preserves existing symbolic links.

If Claude's file contains only one `@file` import, setup follows that import.
It updates the target file and keeps the import. For more complex imports, keep
one reminder in your chosen shared file. Setup does not read a full import tree.

Invalid reminder markers stop setup before it changes the client registration.
Codex setup uses the Codex CLI. Claude setup updates only the `berry-brain` entry
in its user JSON settings.

The reminder has one source in [configure.py](../src/berry_brain/configure.py).
The tool descriptions hold the detailed learning rules.

## Project access

To keep task records in separate projects, give both clients the same project list:

```sh
.venv/bin/berry-brain-configure codex --local --project app-one --project app-two
.venv/bin/berry-brain-configure claude --local --project app-one --project app-two
```

This replaces the client's `berry-brain` registration. Include every project it
needs. Removing access to a project does not delete that project's data.

For another MCP client, register this command as a stdio MCP server:

```sh
.venv/bin/berry-brain --local --identity my-client
```

Add the same `--project` options if needed. Stdio means the client exchanges
messages with the brain through the process input and output.

## Data location

| System | Default database path |
| --- | --- |
| Linux | `$XDG_DATA_HOME/berry-brain/brain.sqlite3`, or `~/.local/share/berry-brain/brain.sqlite3` |
| macOS | `~/Library/Application Support/berry-brain/brain.sqlite3` |
| Windows | `%LOCALAPPDATA%\berry-brain\brain.sqlite3` |

To choose another path, add `--data-dir /absolute/private/directory` to both client
setup commands. Use a local disk outside the source directory.

Do not place the live database on a network share or use file sync to copy it.

For access from several machines, keep one database on one host. Run the MCP
process there through SSH, or use an application that hosts the brain API.

If you already use a server, keep your current `--config` setup. Local mode creates
separate storage. It does not import or replace server data. The host application
owns login checks and deployment settings.

## Privacy

Local mode makes no network requests and sends no telemetry. Recalled text goes
to the AI client. The client can send it to its model provider under its data policy.

The package does not encrypt records. Use disk encryption and account permissions.

| System | Access controls |
| --- | --- |
| Linux and macOS | Data directory mode `0700`; database file modes `0600` |
| Windows | The user's folder access rules, or ACLs |

Project access rules apply to the tools. Local mode trusts the OS account.
Any process that can read the database can read its contents. Use a host with
login checks if clients need separate credentials.

Do not save secrets, private personal facts, raw conversations, or hidden reasoning.
The tool instructions prohibit these records. There is no automatic filter that
can ensure their removal.

## Backup and restore

1. Close the connected clients.
2. Copy the whole data directory to a private backup location.
3. Reopen the clients.

Close the clients before you restore a backup. Keep backups out of Git.
Software updates do not delete saved data.

## Failed connections

A failed connection or invalid server reply does not prove that a save failed.
The server might have saved the data before the connection failed.

When access returns, check the saved state. Or retry with the exact same arguments.
Keep the same event ID and expected version. The client does not retry saves
on its own. For a read request, the warning does not mean that data changed.

Check the URL in the client config and any proxy on that path. A healthy response
from another URL does not prove that the client's path works.
Error messages omit remote error bodies and failed response content.
