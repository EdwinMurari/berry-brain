# Server mode

This is the existing Berry Memory deployment. Local brain users do not need it.
It combines project learning with a separate personal-fact API. It requires
Qdrant and the Berry LLM Gateway; it is not a generic provider endpoint.

Copy `.env.example` to private `.env` and set your config directory, data directory,
and Gateway URL. These values are required by Compose. Keep the config directory
and its token files private. The server runs as UID/GID 1000; provision its data
directories for that owner. Existing installations must retain their current
paths, tokens, grants, collection, and exact model settings.

The config directory contains `berry-memory.env` (Gateway credentials),
`berry-memory-auth.env` (one `identity=token` line per client), and
`brain-policy.json` (private access grants). Use independent random 64-character
hex tokens for clients. Never store these live files in the repository.


Berry Memory stores and retrieves facts selected by the main Matrix agent.
The agent uses the shared memory tool during its normal turn. The explicit-fact
path does not interpret conversations, split sections, or call a generation model.

All saves use the explicit-fact API with exactly one fact and its selected scope.
The requesting person's facts and recommendations use profile ownership.
Job facts use room ownership. Task-only facts use thread ownership.
The agent must keep source, conditions, negation, and uncertainty in its text.
Storage preserves that text and the user/assistant source. Shared reads do not
authorize a write to another person's profile. Delegated workers are read-only.

Ownership validation runs before receipt lookup or storage. Conversation-style
requests are rejected, even if an old receipt exists. The retired `extract:`
receipt prefix remains reserved so an old extraction result cannot become save
evidence. Existing memories and historical receipts are retained.

Mem0 uses its raw `add(infer=False)` API. It requires an LLM object at startup,
so a registered disabled provider rejects every generation call. There is no
fallback model or generation dependency in fact storage. Embeddings use the Berry LLM Gateway
with this service's App Identity. The exact embedding identity is owned by
`server.py` and checked against the Gateway catalogue during health checks.

Exact text is deduplicated within the same owner and source. Durable receipts
recover confirmed saves. Exact-content lookup also recovers a vector write that
completed before its receipt. The main agent corrects a fact by saving the
replacement, then removing only the superseded record by ID. These are two
operations; it must report an incomplete correction if either step fails.

`migrate_ownership.py` applies an operator-reviewed ownership plan. It checks
the original content hash and owner, saves private payload backups under
`/data`, changes only ownership fields, and verifies the result.

```sh
docker compose config --quiet
docker build --target test .
```

The offline checks cover authentication, API limits, ownership, exact text/source
preservation, generation rejection, duplicate recovery, and durable receipts.
Agent fact-selection quality is tested in the Berry Agents repository.

## Shared learning

The same service also owns Berry Brain: project experience, conditional lessons,
and task checkpoints shared by authorized Matrix, Codex, and Claude Code clients.
Personal memory keeps its existing API and ownership. Brain records use a separate
SQLite database at `/data/brain.sqlite3`; no client mounts that database.

`GET /v1/brain/tools` supplies the schemas and operation policy. The same actions
are available as `POST /v1/brain/{action}` and through `brain_client.py`, a portable
stdio MCP adapter whose HTTP mode needs no third-party dependencies. Credentials use the existing
identity-bound `Authorization` and `X-Berry-App` headers. Each client gets its own
identity and token. Configure allowed projects and Matrix room bindings in
`/run/secrets/brain-policy.json`. `brain-policy.example.json` shows synthetic grants. Set your own client identities,
projects, and room IDs in the private live file. Never replace live grants with the example.
No grant means no access. Restart the web unit after changing credentials or grants.

Experiences preserve supplied evidence and author attribution. They are not
independently certified. Proposed lessons stay out of normal recall until helpful
reports from two later task IDs with distinct evidence; their source tasks do not
count. Harmful feedback retires a lesson. This is a conservative reuse rule, not a
statistical claim. Clients must test actual outcomes and preserve counterevidence.
Retrieval uses SQLite full-text search on the supplied query, with a 24 KB total
response budget. Empty queries return task state only. Recent unfinished tasks
appear as short previews; use history for full state. An empty lesson match is valid.
Saved job status is historical and must be checked against the owning service.

Record, checkpoint, proposal, feedback, and revision retries recover saved results
or report a conflict. Checkpoints and revisions require an expected version.
Corrections create a new lesson with `supersedes`; the old lesson retires when the
replacement qualifies. History retains original evidence and changes, with bounded
pagination. A retired lesson can return to candidate for new testing, never directly
to active. Recall does not grant execution authority or change room permissions.

Every five minutes an in-process worker checks for eligible work. At most one
proposal batch starts per hour, with up to eight new experiences from one scope
and 24 KB of input. Oversized experiences stay available for direct review and are
marked skipped for this job. Failed or interrupted batches get one later retry.
There is no extra daemon or model fallback. `BERRY_BRAIN_MODEL` selects the exact
Gateway model; omitting it disables automatic proposals, while direct learning and
recall still work. The worker cannot call tools, edit skills, or activate its output.
Its request, response, source IDs, failures, and retry count are retained. Scoped
history shows the job status without dumping provider payloads into model context.

Skills stay in their owning versioned catalogue. Test a method, link its name and
full Git revision, and follow that catalogue's review and validation process. An active lesson
does not auto-publish a skill. A changed method or revision needs a new proposal.

## Development clients

Create a private token file and a JSON client config outside the source tree:

```json
{
  "url": "https://brain.example.org",
  "identity": "codex",
  "token_file": "/absolute/private/path/brain.token"
}
```

Register `python3 /absolute/path/brain_client.py --config /absolute/path/client.json`
as the `berry-brain` stdio MCP server in Codex or Claude Code. Use a different
identity/config for each client. The adapter also accepts `--call tools` or
`--call recall` with a JSON body on standard input for an integration check.
Native client memory directories remain client-owned generated state.

`configure_client.py` registers the adapter for either client after its credential
and config exist. It validates read access first and does not print credentials.
On a machine without a local credential, its `--ssh-host` option runs the adapter
on an already-authorized Berry SSH host using that host's private client config.
This uses normal development SSH access, not the Matrix broker.

```sh
python3 configure_client.py codex --config /absolute/path/codex.json
python3 configure_client.py claude --config /absolute/path/claude.json
python3 configure_client.py codex --ssh-host my-server --remote-adapter /opt/berry-memory/brain_client.py --config /absolute/private/path/codex.json
```

Setup also installs the short reminder in the selected client's global instructions
on this machine. It preserves other text and updates its own
marked section on repeat runs. With SSH, registration and the global reminder are
local; the adapter and credentials remain on the remote host.

Reopen the client session after registration. The shared tools supply their
learning instructions. Use the same project/task ID to continue a
checkpoint from another client. Required operating rules stay in project guidance.
