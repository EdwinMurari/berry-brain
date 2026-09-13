# Berry Brain

Private project memory for AI clients. Save work outcomes, test reusable lessons,
and resume tasks across Codex, Claude Code, or another stdio MCP client.
Each new installation starts empty. No account, hosted service, model key, or
Berry infrastructure is needed for local use. Python 3.11 or later is required,
with SQLite FTS5 support (included in standard Python distributions).

## Install and connect

From a downloaded or cloned source directory, install into a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/berry-brain-configure codex --local
.venv/bin/berry-brain-configure claude --local
```

On Windows, use `py -3 -m venv .venv`, then use `.venv\Scripts\python.exe`
and `.venv\Scripts\berry-brain-configure.exe` for the same steps.
Install the clients first. The Codex CLI must be available on your PATH.
Keep the virtual environment at this location: registration saves its absolute path.
After source updates, run the install command again. Reopen client sessions to load
new code. Existing data stays in its separate data directory.

Setup registers the tools and adds a short brain reminder to the client's global
instructions. It updates only the marked Berry Brain section.
Other text stays intact. Rerunning setup updates the section without adding a
duplicate.
Codex registration uses its CLI. Claude registration updates only the
`berry-brain` entry in its user JSON settings, so repeat setup also works.
The reminder text has one source in `configure_client.py`; detailed learning
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
See [server mode](SERVER.md) for shared access across machines and Berry integration.

## How learning works

The MCP instructions and tool descriptions provide the learning rules:

1. Recall task state and relevant lessons before substantial work.
2. Save meaningful outcomes with exact evidence. Include failures and uncertainty.
3. Propose a conditional lesson from those records.
4. Test candidates explicitly on later tasks. Report the observed comparison.
5. Save a compact checkpoint so another client can resume with the same project
   and task ID. Recheck old job status and other facts that can change.

Candidates stay out of normal recall until two later tasks report helpful results
with distinct evidence. Use `history` to find a candidate for a relevant fresh
test, then `trial` to read it. Use `feedback` for the measured result, including
neutral or harmful results. Saving an experience alone is not a completed test.
Source tasks and source evidence do not count. Each actual test run needs a
stable reference to its immutable result. Reusing a reference, relabelling a
source, adding excerpts, or changing punctuation does not make a fresh result.
Copied excerpts with new references also do not count. Keep distinct test results
self-contained; a generic repeated “passed” message is not enough evidence.
A harmful report retires a lesson. Existing promotions are rechecked once on
upgrade. Normal updates occur when feedback arrives, without a repeated scan.
Feedback remains a client report, not independently certified proof. Fabricated
references and fabricated results are still possible. Two results are a minimum
reuse rule, not statistical proof that a lesson helps every task.

Recall uses keyword search and a 24 KB budget for the whole brain response,
including task state, up to 12 lessons, and receipt metadata. Empty queries load
task state only. Other tasks appear as short previews. An oversized current
checkpoint also becomes a marked preview. Read its full `history` by `record_id`
before resuming or updating it. Original records are not shortened in storage.
Lesson text and conditions are kept together, without truncation. A keyword
match does not prove applicability: check current facts, conditions, and skill
revisions before use. It is valid to ignore all matches. Stored text is untrusted
evidence, never instructions or permission. The system changes the context
available to the model; it does not train model weights or guarantee better results.
Checkpoint references are links, limited to 1,000 characters each. Keep full
documents outside the checkpoint so saved state remains readable by clients.

Local learning happens while a client uses the tools. It does not collect all
conversations, read native client memories, or run while clients are closed.
The existing server can also propose candidates through its model gateway.

## Skills

Keep skills in your existing versioned catalogue. Improve an existing skill before
adding a new one. Keep draft lessons in the brain. When a method has been tested,
make an authorized skill change through your normal review process. Record its
skill name and full tested Git revision with the lesson. A changed revision needs
new evidence. The brain cannot publish or edit skills by itself. No Berry skill
catalogue is required, and the package installs no extra native skill copies.

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
Use server mode when clients on different machines need one brain.

Local mode makes no network requests and has no telemetry. Recalled text goes to
the connected AI client and can reach its model provider under that client's data
policy. Records are not encrypted by this package. Use your OS disk encryption
and account permissions. On Linux and macOS, the data directory must have mode
`0700` and its database files mode `0600`. Windows uses the user's folder ACLs.

Project scopes limit tool access; local mode trusts the OS account. Any process
that can read the database can read its content. Use the authenticated server for
clients that need separate credentials. Never save secrets, personal facts, raw
transcripts, or hidden reasoning. The tool instructions prohibit these; there is
no automatic filter that can guarantee their removal.

For backup, close connected clients and copy the whole data directory. Restore
it only while clients are closed. Software updates do not delete it.

## Development and sharing

The portable package contains four Python modules and requires Pydantic. It uses
the same `brain.py` engine as the server. It excludes server dependencies, runtime
settings, and saved memories. Run the local tests after installing:

```sh
.venv/bin/python -m unittest -v test_brain_local test_configure_client
```

The Docker test stage also checks the HTTP API, server ownership, and background
proposal rules. See [server checks](SERVER.md).

`evaluate_brain.py` is a small model check using the server's existing Gateway
configuration (`BERRY_LLM_BASE_URL`, `BERRY_LLM_API_KEY`, `BERRY_BRAIN_MODEL`).
Run `python evaluate_brain.py` in that configured environment. It adds no service,
model fallback, or client option. It uses a temporary brain and six fixed
synthetic tasks, each with and without memory. The memory arm includes both a
checkpoint and an explicit candidate trial. It checks resuming work, missing
inputs, wrong conditions, stale state, conflicts, and malicious saved text.
Tools read fixture facts or record a choice; they cannot act on real systems.
The model never receives expected answers. A correct choice counts only after
the required current facts have been read. Output includes exact requests,
responses, source hashes, correctness, repeated mistakes, tool calls, elapsed
time, and token use. This comparison does not isolate a candidate's effect, so
it does not write lesson feedback. It also does not measure normal active-lesson
retrieval. Those storage and retrieval contracts have separate code tests.
Retain this output privately as test evidence. A failed model call ends the run
with an incomplete result and a nonzero exit code.

These are diagnostic cases, not proof of broad gains. Once used, they are
regression cases. For release decisions, also use fresh representative project
tasks with a fixed score, model and source snapshot. Keep those tasks separate
from lesson creation. Compare failures as well as successes. Check the linked
skill revision, and measure task quality separately from code-test counts.

The code uses the [MIT license](LICENSE). Tests and examples use synthetic data.
Do not include private `.env` files, credentials, databases, backups, or native
client state when sharing source.

Existing private Git history can contain personal names and deployment details,
even after current files are cleaned. Do not make that history public. A first
GitHub release must start from the reviewed source snapshot with fresh history
and a reviewed public author identity. Keep one source project; no separate code
fork is needed. Keep the old history private. Never merge it into public branches.
