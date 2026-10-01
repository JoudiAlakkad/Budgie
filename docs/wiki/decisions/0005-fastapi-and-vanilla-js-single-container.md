# 0005 — FastAPI and vanilla JS in a single container

**Status:** Accepted (2026-09-28)

## Context
The brief values correct behaviour, architecture and evaluation over visual polish. It requires a UI, a documented API and a container definition.

## Decision
- The backend is FastAPI with Pydantic, which generates the OpenAPI documentation automatically.
- The UI is static HTML, vanilla JS and one CSS file, served by the same FastAPI app. There is no build step and no framework.
- It all runs as one container, `python:3.12-slim` with uvicorn, publishing port 8000.

## Consequences
- The setup is minimal, and one `docker compose up` runs the whole service.
- The UI stays simple. Charts use Chart.js, vendored or loaded from a CDN.
- Related: [0001](0001-monolith-layout-with-per-stack-agent-context.md).
