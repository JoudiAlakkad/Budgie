# Wiki

Durable knowledge for this project. Index only — content lives in the linked
files.

Written by the main session only. Subagents read freely and report findings
back; the parent records them.

## Plan

- [Roadmap](plan/roadmap.md) — problem, responsibility, features F0–F12
- [Workflow](plan/workflow.md) — how a feature is built, subagents, CI, dev log
- [Original plan (2026-09-28)](plan/original-plan.md) — frozen snapshot, not maintained

## Architecture

- [Architecture](architecture.md) — layers, data flow, data encapsulation
- [Uncertainty](backend/uncertainty.md) — how model uncertainty is handled (criterion 13)

## Decisions

- [0001 — Monolith layout with per-stack agent context](decisions/0001-monolith-layout-with-per-stack-agent-context.md)
- [0002 — Harness config stays at the root](decisions/0002-harness-config-stays-at-the-root.md)
- [0003 — Custom subagents per stack](decisions/0003-custom-subagents-per-stack.md)
- [0004 — Vision model direct via Ollama](decisions/0004-vision-model-direct-via-ollama.md)
- [0005 — FastAPI and vanilla JS in a single container](decisions/0005-fastapi-and-vanilla-js-single-container.md)
- [0006 — SQLite behind a repository layer](decisions/0006-sqlite-behind-repository-layer.md)
- [0007 — Async extraction with polling](decisions/0007-async-extraction-with-polling.md)
- [0008 — Rule-based review status, not a probability](decisions/0008-rule-based-review-status-not-probability.md)
- [0009 — Contract-first parallel development](decisions/0009-contract-first-parallel-development.md)
- [0010 — CI gates before merge](decisions/0010-ci-gates-before-merge.md)
- [0011 — Sandbox hardening](decisions/0011-sandbox-hardening.md)
- [0012 — Dev-log skill](decisions/0012-dev-log-skill.md)
- [0013 — Deterministic item categorisation by lookup](decisions/0013-deterministic-item-categorisation-by-lookup.md)
- [0014 — Status line skill](decisions/0014-statusline-skill.md)
- [0015 — Non-receipt is a failure with retry or manual entry](decisions/0015-non-receipt-is-a-failure-with-retry-or-manual-entry.md)
- [0016 — API representation and stub convention](decisions/0016-api-representation-and-stub-convention.md)
- [0017 — Personal data is kept out by the schema and redacted by code](decisions/0017-personal-data-is-redacted-by-code.md)
- [0018 — Review form editing semantics](decisions/0018-review-form-editing-semantics.md)
- [0019 — Lean extraction: line totals, date as printed, Pfand](decisions/0019-lean-extraction-line-totals-date-as-printed.md)
- [0020 — Saving user category choices and the duplicate rule](decisions/0020-user-category-choices-and-duplicate-rule.md)
- [0021 — Dashboard and goal semantics](decisions/0021-dashboard-and-goal-semantics.md)
- [0022 — Demo data by an explicit command through the services](decisions/0022-demo-data-by-explicit-command.md)

## Areas

- [Contracts](contracts/) — the frontend/backend seam
- [Frontend](frontend/)
- [Backend](backend/)

## Project evidence

- [AI development log](../ai-dev-log.md) — add episodes with `/dev-log <title>`
