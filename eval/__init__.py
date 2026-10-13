"""The extraction evaluation (F11; docs/wiki/backend/evaluation.md, decision 0028).

A top-level package beside `backend/`: it runs the app's own extractor and
`process_extraction` on hand-labelled receipts in `data/eval/` (git-ignored), scores the
answers, and writes `eval/results/` and the generated block of `docs/evaluation.md`.
It may import `app.ai`, `app.config`, `app.domain` and `app.services`, never `app.api` or
`app.db` (import-linter); `app` never imports it.
"""
