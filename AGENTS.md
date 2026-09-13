# Berry Memory

This repository owns the portable Berry Brain package and the optional Berry Memory server.
Both modes use the same engine in `brain.py`. Keep learning rules, schemas, and storage
in that engine. Keep local setup separate from server deployment.

For development and deployment on a Berry host, use `$berry-development` when available.
Other installations need no Berry skills, account, or services. See `README.md` for
local setup and `SERVER.md` for server operation.

Local gotchas:

- Scoped storage, raw-save behavior, and the embedding model identity belong here.
- Local mode makes no model calls. In server mode, reach models only through the Berry LLM Gateway with this unit's app identity; do not add direct runtime access or provider credentials.
- Use synthetic examples. Keep credentials, host paths, access grants, memories, and database sidecars outside source control.
