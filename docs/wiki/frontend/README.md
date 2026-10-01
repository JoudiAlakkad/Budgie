# Frontend

Static HTML, vanilla JS and one CSS file in `frontend/`, served by the FastAPI app ([0005](../decisions/0005-fastapi-and-vanilla-js-single-container.md)). There is no build step. The frontend talks to the backend only through [contracts](../contracts/).

- [Pages](pages.md): upload, review, expenses, dashboard, settings
- [API client](api-client.md): fetch wrapper, polling, error display, AI labelling

The stack conventions are in `frontend/CLAUDE.md`.
