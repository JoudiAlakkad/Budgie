# Roadmap

## Problem
People trying to stay within a budget or reach a savings goal lose track of spending that exists only on paper receipts. Logging receipts by hand takes too long, so recurring or excessive expenses ("budget leaks") go unnoticed.

## Service responsibility
> **Budgie turns receipt photos into structured, user-verified expense records and flags where spending leaks out of the user's budget.**

- **AI's role:** a local vision model extracts merchant, date, line items and totals from the receipt image ([0004](../decisions/0004-vision-model-direct-via-ollama.md)).
- **Deterministic logic:** validation, review status, item categorisation, duplicates, budgets and leak detection ([domain-logic](../backend/domain-logic.md)).

## Core scenarios
1. **Scan and correct a receipt:** upload a photo, the AI extracts the data, and the user reviews it in the UI. Flagged fields and uncategorised items are highlighted. The user corrects them and confirms, and the expense is saved.
2. **Budgets and overspend:** the user sets monthly limits per category. The dashboard shows spending against each limit and a forecast for the month.
3. **Discover a leak:** after a few months of data, Budgie flags a recurring or growing pattern, for example "12 snack purchases this month, 38 € (+60 % vs. your median)".

## Features
Each feature is one issue, one branch (`feat/F<NN>-<slug>`) and one PR. The process is described in [workflow](workflow.md).

| ID | Feature | Depends on | Built by |
|---|---|---|---|
| F0 | Repo, sandbox hardening, wiki, agent config, dev-log skill | – | main session |
| F1 | Service skeleton: config, health, DB session, Dockerfile, pytest, CI | F0 | `backend-dev` |
| F2 | Contracts: API schemas, error format, receipt lifecycle, `contracts/` pages | F1 | Plan agent drafts, main session decides |
| F3 | LLM client + extractor: prompts, JSON schema, repair, recorded-response tests | F2 | `backend-dev` |
| F4 | Validation + review status | F2 | `backend-dev` (parallel with F3, F7) |
| F5 | Receipt pipeline: upload → background extraction → persist, failure handling | F3, F4 | `backend-dev` |
| F6 | Upload + review/correct UI, corrections, confirm | F5 | `backend-dev` ∥ `frontend-dev` |
| F7 | Name normalisation, category lookup table + seed, duplicates | F2 | `backend-dev` (parallel with F3, F4) |
| F8 | Budgets, savings goal, dashboard | F6, F7 | `backend-dev` ∥ `frontend-dev` |
| F9 | Leak detection + insight cards, demo seed data | F8 | `backend-dev` ∥ `frontend-dev` |
| F10 | CSV export + OpenAPI export | F5 | `backend-dev` |
| F11 | Evaluation dataset, runner, comparisons, report | F5 | `backend-dev` + `eval-runner`; the student labels the ground truth and interprets the results |
| F12 | README, responsible design, compose file, slides | all | main session |

## Acceptance criteria, by feature (summary)
- **F1:**
  - `docker build` and `docker run` work, and `GET /api/health` returns `{app, db, llm}`.
  - `make check` passes, and CI is green.
- **F2:**
  - Every endpoint in [api-endpoints](../contracts/api-endpoints.md) has request and response DTOs, and the error format is shared.
  - `docs/openapi.json` is generated.
- **F3:**
  - Recorded responses cover these cases: valid, malformed (repaired), malformed (repair fails), missing fields and `is_receipt=false`.
  - Timeout and connection errors are mapped to typed errors.
- **F4:**
  - Table-driven tests cover each rule in [domain-logic](../backend/domain-logic.md).
  - The review status is never shown as a number.
- **F5:**
  - Every row of the failure-handling table in [ai-extraction](../backend/ai-extraction.md) has an API test.
- **F6:**
  - The user can run the whole of scenario 1 in the UI.
  - Confirm is refused while any item is uncategorised.
  - AI-generated values are labelled.
- **F7:**
  - Normaliser tests cover real receipt strings, for example `BIO BANANE 1 KG → banane`.
  - A user's choice is saved and used on the next receipt.
- **F8, F9:**
  - Scenarios 2 and 3 work on the seeded demo data.
  - Each leak card shows its explanation.
- **F10:**
  - The CSV columns match [csv-export](../contracts/csv-export.md).
- **F11:**
  - There are at least 10 cases (target about 20), with aggregated metrics, at least 2 comparisons, and a failure discussion in `docs/evaluation.md`.
- **F12:**
  - The README covers every item of criterion 18 in the brief.

## Milestones (about 120 h)
| # | Milestone | Hours | Features |
|---|---|---|---|
| 1 | Foundations | 10 | F0, F1 |
| 2 | AI spike: run the model on 5 receipts, measure latency, confirm [0004](../decisions/0004-vision-model-direct-via-ollama.md) and [0007](../decisions/0007-async-extraction-with-polling.md) | 10 | – |
| 3 | Extraction pipeline | 20 | F2, F3, F4, F5 |
| 4 | Domain logic | 20 | F7, and the backend parts of F8 and F9 |
| 5 | API + UI | 25 | F6, F8, F9, F10 |
| 6 | Evaluation | 15 | F11 |
| 7 | Docs and hardening | 10 | F12 |
| 8 | Buffer and slides | 10 | – |

Progress is tracked in the repo's issues and milestones, not on this page.
