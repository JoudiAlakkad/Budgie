# Architecture

Budgie is one service in one container ([0005](decisions/0005-fastapi-and-vanilla-js-single-container.md)), with a layered backend.

```
┌──────────── Budgie container (port 8000 only) ─────────────────┐
│  Browser UI (frontend/ static HTML+JS)                          │
│        │  HTTP /api/* only                                      │
│        ▼                                                        │
│  api/        FastAPI routers + schemas.py (public DTOs)         │
│        ▼                                                        │
│  services/   use cases: receipt pipeline, corrections, insights │
│     │            │                         │                    │
│     ▼            ▼                         ▼                    │
│  domain/      db/ (repositories,        ai/ (httpx client,      │
│  pure rules   SQLAlchemy)               prompts, schema)        │
│               ▼                           │                     │
│   volume budgie-data:/data                │                     │
│   ├─ budgie.db (SQLite)                   │                     │
│   └─ uploads/ (receipt images)            │                     │
└───────────────────────────────────────────┼─────────────────────┘
                                            ▼  OpenAI-compatible /v1
                                     Ollama (host or own container)
External clients ──► REST API / CSV export only
```

## Layers
| Layer | Responsibility | May import |
|---|---|---|
| `api/` | HTTP only: validate input, call services, return DTOs | `services`, `api.schemas` |
| `services/` | Use cases; the only layer that combines domain, db and ai | `domain`, `db`, `ai` |
| `domain/` | Pure deterministic rules | nothing app-internal |
| `db/` | SQLAlchemy models and repositories | – |
| `ai/` | OpenAI-compatible client, prompts, output schema | – |

The module details are in [backend/modules](backend/modules.md).

## Main data flow (scenario 1)
1. The UI sends `POST /api/receipts` (multipart). The service stores the image, creates a receipt with status `uploaded`, and returns `202` ([0007](decisions/0007-async-extraction-with-polling.md)).
2. A background task sets the status to `extracting` and calls `ai.extractor`. The model returns JSON, which is validated against `ReceiptExtraction` and repaired once if it's malformed.
3. `domain.validation` runs the arithmetic and date checks. `domain.categorize` normalises each item name and looks it up ([0013](decisions/0013-deterministic-item-categorisation-by-lookup.md)). `domain.confidence` sets the review status and the flags ([0008](decisions/0008-rule-based-review-status-not-probability.md)). If the model reports `is_receipt=false` or the plausibility rule fails, the receipt becomes `failed` with `not_a_receipt` instead ([0015](decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)).
4. The expense, its line items and the raw model output are saved, and the status becomes `extracted`.
5. The UI, which has been polling `GET /api/receipts/{id}`, shows the review form. The user corrects and categorises items and calls `POST /api/expenses/{id}/confirm`.
6. Confirmed expenses feed `domain.budget` and `domain.leaks`, which serve `GET /api/insights/*`.

The status transitions are listed in [contracts/receipt-lifecycle](contracts/receipt-lifecycle.md).

## Data encapsulation
The storage is private to the service ([0006](decisions/0006-sqlite-behind-repository-layer.md)).
- Only `app/db/` touches SQLAlchemy, and the API returns DTOs only. `import-linter` enforces this in CI.
- The static mount serves only `frontend/`. Images are served only through `GET /api/receipts/{id}/image`.
- SQLite has no network port. The volume is mounted only by the app container, which runs as a non-root user, and `/data` has mode `700`.
- `*.db` and `data/uploads/` are git-ignored. Demo data is recreated by the seed script, which goes through the service code.

## Integration points
Budgie is the only service. Anything outside it must use:
- the REST API ([contracts/api-endpoints](contracts/api-endpoints.md)), documented as OpenAPI at `/docs` and in `docs/openapi.json`
- the CSV export ([contracts/csv-export](contracts/csv-export.md))

No other component may read the database, the uploads directory or internal classes.
