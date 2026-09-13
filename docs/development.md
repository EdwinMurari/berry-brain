# Development

[Home](../README.md) · [Setup and data](usage.md) · [Learning](learning.md)

## Source files

The package has one brain engine. Local clients and host applications use it.

| Path | Purpose |
| --- | --- |
| [engine.py](../src/berry_brain/engine.py) | Records, recall, access checks, and lesson rules |
| [learning.py](../src/berry_brain/learning.py) | Candidate generation within set limits |
| [local.py](../src/berry_brain/local.py) | Local paths, file permissions, and client access |
| [client.py](../src/berry_brain/client.py) | MCP messages and HTTP requests |
| [configure.py](../src/berry_brain/configure.py) | Client setup and shared reminder text |
| [tests/](../tests/) | Offline tests with synthetic data |
| [docs/](./) | Setup, learning, and development guides |
| [.github/](../.github/) | Automated test workflow |
| [pyproject.toml](../pyproject.toml) | Package details, dependencies, and commands |

The package requires Pydantic. It has no HTTP framework, model SDK, database
service, environment file, or deployment stack.

## Run checks

Install the package before testing. Its source is under `src/`.
Tests use the installed code and start real MCP processes.

From the source directory:

```sh
.venv/bin/python -m pip install .
.venv/bin/python -m unittest discover -s tests
```

See [Setup](usage.md#install) to create the environment or use Windows commands.

The tests run offline with synthetic data. They need no model credentials or
private services. GitHub Actions runs them on Linux, macOS, and Windows.
It also checks Python 3.11, the minimum supported version.

## Build a release

Use the Python build tool:

```sh
.venv/bin/python -m pip install build
.venv/bin/python -m build
```

## Connect a host application

Import `Brain` from `berry_brain.engine`. Supply a database path and an explicit
client access policy. Call the same engine that local MCP clients use.

Client names grant no special access. The `room_local` policy enables room scopes.
It requires a matching project room grant.

The host must verify callers. It must take room IDs from trusted request context
before it passes them to the engine.

For HTTP access, the client config has three fields:

| Field | Purpose |
| --- | --- |
| `url` | Brain service URL |
| `identity` | Client identity |
| `token_file` | Path to the private token file |

The HTTP client sends `Authorization: Bearer ...` and `X-Brain-Client` headers.
The host must provide these routes with the engine's request and response format:

- `GET /v1/brain/tools`
- `POST /v1/brain/{action}`

The host application owns hosting code, model calls, credentials, and deployment
settings. Keep them outside this package.

## Check model quality

Code tests check storage and learning rules. They do not prove that memory improves
model results.

To measure that, compare new tasks with and without memory. Choose tasks that
represent real work. Keep the model and scoring rules fixed.
Measure failures, answer quality, time, and cost.

Do not use the examples that created a lesson as new proof that it helps.

## Share source safely

The code uses the [MIT license](../LICENSE). Tests and examples use synthetic data.
Keep these files out of shared source:

- Private `.env` files and credentials.
- Databases and backups.
- Native client state.

Private Git history can contain personal names and deployment details, even after
you clean the current files.

When first publishing a private project, start with a reviewed source snapshot and
fresh history. Check the public author identity. Keep the old history private.
Never merge it into public branches.

Keep one source project. A separate code fork is not needed.

## Upgrade from 0.2

Version 0.3 moves the modules into the `berry_brain` package. The CLI command names
and database paths stay the same.

1. Install the update.
2. Repeat your existing `berry-brain-configure` commands.
3. Reopen client sessions.

This replaces registrations that point to the old module file. The new registration
uses Python's isolated mode. This stops files in a task directory from taking the
place of the installed package.

Update library imports:

| Old import | New import |
| --- | --- |
| `brain` | `berry_brain.engine` |
| `brain_learning` | `berry_brain.learning` |
| `brain_local` | `berry_brain.local` |
| `brain_client` | `berry_brain.client` |
| `configure_client` | `berry_brain.configure` |

Update host launchers to `python -I -m berry_brain.client`. Keep the existing
arguments and use the Python executable from the installed environment.
The database format and learning rules do not change for this source layout.

## Request errors

The adapter rejects invalid requests before it can save data. It follows the
[MCP request ID rules](https://modelcontextprotocol.io/specification/2025-06-18/basic)
and uses [JSON-RPC error codes](https://www.jsonrpc.org/specification).

HTTP errors expose only the status code. A remote error body can contain reflected
credentials. The adapter does not pass that body to clients.
