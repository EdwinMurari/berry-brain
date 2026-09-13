# Berry Brain

This project owns the reusable brain engine, local storage adapter, stdio MCP
client, and client setup. Keep one implementation of each learning rule.

- Keep hosting, authentication services, model-provider transport, and deployment
  settings in the consuming application. Do not add homelab-specific defaults.
- Client names do not grant special access. Permissions come from explicit policy.
- Preserve stored data and the evidence rules when changing package versions.
- Use synthetic data. Never commit credentials, databases, or private client state.
- Run `python -m unittest discover -s tests`
  in an environment with the package installed.
