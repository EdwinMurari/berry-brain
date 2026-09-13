# Project rules

This project owns the shared brain engine, local storage adapter, stdio MCP client,
and client setup. Keep one implementation of each learning rule.

- Keep hosting, login services, model calls, and deployment settings in the host application.
- Do not add defaults for a specific home lab.
- Grant access through explicit policy. Client names grant no special access.
- Preserve saved data and evidence rules when changing package versions.
- Use synthetic test data.
- Never commit credentials, databases, or private client state.

Install the package in the test environment. Then run:

```sh
python -m unittest discover -s tests
```
