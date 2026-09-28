# Development

[Home](../README.md) · [Setup and data](usage.md) · [Learning](learning.md)

## Source files

The package has one brain engine. Local clients and host applications use it.

| Path | Purpose |
| --- | --- |
| [engine.py](../src/berry_brain/engine.py) | Records, recall, access checks, and lesson rules |
| [learning.py](../src/berry_brain/learning.py) | Candidate generation within set limits |
| [selection.py](../src/berry_brain/selection.py) | Shared lesson selector and typed evaluator interface |
| [evaluation_http.py](../src/berry_brain/evaluation_http.py) | Configurable evaluation HTTP adapter |
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

## Edit the product diagrams

Open the scene in Excalidraw:

- [Prompt to saved memory](images/how-it-works.excalidraw).
- [Experience to reusable lesson](images/learning-flow.excalidraw).

Save the edited scene and export its matching SVG under `docs/images/` with a
light background. Keep each scene and SVG together in the same commit. The README
embeds both diagrams and links to their editable scenes. The learning guide reuses
the lesson diagram. Check each image at the README's display size so labels fit
inside their boxes and remain readable. Keep the SVG title and description useful
for readers who cannot see the image.

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

The host application owns hosting code, credentials, and deployment settings.
Lesson selection uses the package's shared selector with an evaluation adapter.
Other host model calls, such as background proposal generation, remain host-owned.

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

## Optional lesson selection

Use `LessonSelector(evaluator)` for both local and hosted selection. The selector
owns the questions, input bounds, and answer validation. An evaluator implements
the typed `Evaluator` protocol: `evaluate(state, questions)` returns a map of typed
Choice answers, or `None` when explicitly disabled. It raises on failure. Each
answer has `type: "choice"` and `choice: "keep" | "drop" | "uncertain"`.

```python
from berry_brain.engine import Brain
from berry_brain.selection import LessonSelector

# evaluator is your configured HTTP client or gateway adapter.
brain = Brain(path, policy, selector=LessonSelector(evaluator))
```

This is dependency injection through a typed interface. Transport adapters can
change without copying the lesson-selection policy. It is a design pattern, not
a claim that all providers implement one industry-standard evaluation API.
The built-in `HttpEvaluator` implements the documented
[TypeSafe contract](https://docs.typesafe.ai/api); Vercel also offers a
[compatible endpoint](https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe).
Adapters for other contracts must verify their requested and returned model
identities, usage, and response status before returning answers. They own bounded
network operations and credential handling; they must not silently switch models.
Berry's private adapter uses its LLM Gateway and the same `LessonSelector`.

Selection receives `query`, `current_context`, a historical `saved_checkpoint`,
and allowed active lessons with their IDs, text, and conditions. Only `drop`
removes a lesson from this response. It does not delete or retire it. The engine
still validates the returned IDs and choices at its boundary.

The HTTP adapter has no provider-specific defaults. Credentials stay outside
source and are supplied by private file or environment reference. Local mode
without selection configuration stays offline. See [setup](usage.md#optional-model-selection).

The engine calls the selector outside its database transaction. Before returning,
it checks the current checkpoint and candidate versions again. If state changed,
it uses fresh normal recall. It also uses normal recall if selection fails or
returns invalid IDs or choices. The response reports `unavailable` or
`state_changed` when that happens. Receipts cover only returned lessons.

An empty query or no matching lessons makes no selection call. Responses still
fit within 24 KB. Selection cannot find lessons missed by keyword search.
