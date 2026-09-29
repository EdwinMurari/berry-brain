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

## Optional model selection

Use the same lesson selector locally or in a server application. You choose where
the evaluation runs. Basic memory works without it.

1. Save your provider API key in a private `model.token` file outside the source
   directory. On Linux or macOS, give it mode `0600` (`chmod 600 model.token`).
   On Windows, restrict access to your account through file permissions.
2. Create `evaluation.json` beside it. This example uses TypeSafe directly:

   ```json
   {
     "url": "https://api.typesafe.ai/v1/systemone",
     "model": "jev-1.13.0",
     "token_file": "model.token"
   }
   ```

3. Pass the config path when registering each client:

   ```sh
   .venv/bin/berry-brain-configure codex --local --selection-config /absolute/private/evaluation.json
   .venv/bin/berry-brain-configure claude --local --selection-config /absolute/private/evaluation.json
   ```

4. Reopen the client sessions. Selection runs when recall finds matching lessons.
   It can also suggest feedback after you save an experience.
   Setup checks the configuration and credential without making a model call.

Keep your existing `--project` and `--data-dir` options when re-registering. Other
MCP clients can pass `--selection-config` to `berry-brain --local` directly.

The adapter sends `model`, `state`, and typed `questions`. It expects matching
`answers`, the response `model`, and `usage.input_tokens` / `usage.output_tokens`.
These providers expose that same HTTP contract:

| Provider | Full endpoint URL | Example model ID |
| --- | --- | --- |
| [TypeSafe](https://docs.typesafe.ai/api) | `https://api.typesafe.ai/v1/systemone` | `jev-1.13.0` |
| [Vercel AI Gateway](https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe) | `https://ai-gateway.vercel.sh/typesafe/v1/systemone` | `typesafe-ai/jev` |
| Compatible service | Your evaluation endpoint | Its exact model ID |

Use the credential for your selected provider. There is no fixed provider allowlist
or default endpoint. A chat-completion URL is not an evaluation endpoint.
For a different API, implement the [evaluator interface](development.md#optional-lesson-selection).

`token_file` is relative to the config file unless it is an absolute path.
Alternatively, set `token_env` to an environment variable name instead of
`token_file`. The MCP process must inherit that variable; GUI clients might need
their launch environment configured. Never put a literal key in the JSON or CLI
arguments. The adapter reloads the referenced key for each request.

The returned model must match `model`. If your provider resolves an alias to a
different ID, set `response_model` to that exact expected ID. No alternate model
is selected automatically. Invalid replies, network failures, and timeouts leave
normal recall available with `selection.status` set to `unavailable`. An uncertain
answer keeps an active lesson. Dropping it only affects that response. A candidate
needs keep. If feedback advice fails, the saved experience is unchanged and
`feedback_advice.status` is `unavailable`.

Each request is bounded to 60 KB, with at most 30 KB of state, waits at most five seconds for network operations,
and has no retries or redirects. The response is bounded to 1 MiB. HTTPS is required
except for loopback development endpoints.

To disable selection, repeat your local setup command without `--selection-config`
and reopen the client. Saved memory remains intact. For `--config` server clients,
selection is configured by the server; it is not applied again by each client.

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

Local mode makes no network requests unless you enable model selection. There is
no telemetry. Selection sends the query, supplied current context, historical
checkpoint, and matched lesson texts and conditions to the configured evaluator.
After you save an experience, it sends that experience and the lessons shown for
its task.
It does not send the whole database or task history. Your provider's data policy
applies. Recalled text also goes to the AI client, which can send it to its own
model provider under its data policy.

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
