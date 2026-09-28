# Backend

A Python 3.12 FastAPI app in `backend/app/`. It serves the API and the static frontend ([architecture](../architecture.md)).

- [Modules](modules.md): package layout and dependency direction
- [AI extraction](ai-extraction.md): model client, prompts, output schema, failure handling
- [Domain logic](domain-logic.md): validation, review status, categorisation, duplicates, budgets, leaks
- [Persistence](persistence.md): SQLite schema, uploads, error mapping
- [Configuration](configuration.md): environment variables

The stack conventions are in `backend/CLAUDE.md`. The API shapes are in [contracts](../contracts/).
