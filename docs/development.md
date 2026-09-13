# Development and integration

[Home](../README.md) · [Setup and data](usage.md) · [Learning](learning.md)

## Source map

| File | Purpose |
| --- | --- |
| [`engine.py`](../src/berry_brain/engine.py) | Records, recall, access checks and lesson evidence rules |
| [`learning.py`](../src/berry_brain/learning.py) | Bounded candidate generation with a host-supplied generator |
| [`local.py`](../src/berry_brain/local.py) | Local database paths, permissions and client policy |
| [`client.py`](../src/berry_brain/client.py) | Stdio MCP and HTTP client transport |
| [`configure.py`](../src/berry_brain/configure.py) | Client registration and the shared reminder text |
| [`tests/`](../tests/) | Offline tests with synthetic data |

## Checks

The package has five focused modules and requires Pydantic. It has no HTTP
framework, model SDK, database service, environment file, or deployment stack.

Run the offline checks after installation:

```sh
.venv/bin/python -m unittest discover -s tests
```

Build distributable files with the standard Python build tool:

```sh
.venv/bin/python -m pip install build
.venv/bin/python -m build
```

The source uses a `src/` layout. Install the package before running tests.
This makes checks use installed code, including real MCP subprocesses.
GitHub Actions runs the offline suite on Linux, macOS and Windows.
It also checks the minimum supported Python version, 3.11.
No model credentials or private services are used in these checks.

## Host integration

Applications can import `Brain` from `berry_brain.engine`, supply a database path and an
explicit client policy, and call the same engine used by local MCP clients.
Client names have no special privileges. `room_local` policy enables room scopes
and requires a matching project room grant. Hosts must authenticate callers and
bind room IDs to trusted request context before passing them to the engine.

The HTTP client uses `Authorization: Bearer ...` and `X-Brain-Client` headers.
Its config contains `url`, `identity`, and `token_file`. The host implements
`GET /v1/brain/tools` and `POST /v1/brain/{action}` with the engine's contract.
Hosting code, provider transport, credentials, and deployment settings belong to
the consuming application. They are not part of this package.

## Quality and sharing

Code tests check storage and learning rules. They do not prove better model
results. For that, compare fresh representative tasks with and without memory.
Keep the model and scoring fixed. Measure failures, output quality, time, and
cost. Do not use lesson-creation examples as fresh evidence of improvement.

The code uses the [MIT license](../LICENSE). Tests and examples use synthetic data.
Do not include private `.env` files, credentials, databases, backups, or native
client state when sharing source.

Existing private Git history can contain personal names and deployment details,
even after current files are cleaned. Do not make that history public. A first
GitHub release must start from the reviewed source snapshot with fresh history
and a reviewed public author identity. Keep one source project; no separate code
fork is needed. Keep the old history private. Never merge it into public branches.

## Upgrade from 0.2

Version 0.3 moves the modules into the `berry_brain` package. It keeps the CLI
command names and database paths. Install the update, rerun the same
`berry-brain-configure` commands, and reopen client sessions. This replaces
registrations that point to the old module file. The new registration uses
Python's isolated mode so files in a task directory cannot shadow the package.

Applications that import the library must update their imports:

| Old import | New import |
| --- | --- |
| `brain` | `berry_brain.engine` |
| `brain_learning` | `berry_brain.learning` |
| `brain_local` | `berry_brain.local` |
| `brain_client` | `berry_brain.client` |
| `configure_client` | `berry_brain.configure` |

Update host launchers to `python -I -m berry_brain.client` with the existing
arguments. Use the Python executable from the installed environment.
The database and learning rules do not need a new format for this layout.

The adapter rejects malformed requests before it can save data. It uses the
[MCP request ID rules](https://modelcontextprotocol.io/specification/2025-06-18/basic)
and [JSON-RPC error codes](https://www.jsonrpc.org/specification).
HTTP error responses expose only the status code. A remote error body can
contain reflected credentials, so the adapter does not pass that body to clients.
