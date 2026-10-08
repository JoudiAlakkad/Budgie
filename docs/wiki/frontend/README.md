# Frontend

Static HTML, vanilla JS and one CSS file in `frontend/`, served by the FastAPI app ([0005](../decisions/0005-fastapi-and-vanilla-js-single-container.md)). There is no build step. The frontend talks to the backend only through [contracts](../contracts/).

The files are served with `Cache-Control: no-cache` (`NoCacheStaticFiles` in `app/main.py`, F08), so the browser revalidates each one and gets a cheap `304` when it is unchanged. Without it, browsers cached unchanged modules heuristically: in F08 an old cached `js/dom.js` without `formatDate` broke the module imports of the home page and the dashboard, which then did nothing, with no error banner. After a frontend change made before this fix, a hard reload (Ctrl+Shift+R) clears such a stale copy.

- [Pages](pages.md): upload, review, expenses, dashboard, settings
- [API client](api-client.md): fetch wrapper, polling, error display, AI labelling

The stack conventions are in `frontend/CLAUDE.md`.
