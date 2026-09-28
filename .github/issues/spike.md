title: AI spike: run the model on 5 receipts, measure latency
milestone: M2 AI spike
labels: ai
---
## Goal
Before building the pipeline, run the vision model on 5 real receipts and check that decisions 0004 and 0007 hold.

**Depends on:** F01
**Built by:** student + main session

## Acceptance criteria
- [ ] Model output on 5 receipts is recorded (reusable as F03 test fixtures)
- [ ] Latency per receipt is measured
- [ ] 0004 and 0007 are confirmed or revised in the wiki

## Read first
- `docs/wiki/decisions/0004-vision-model-direct-via-ollama.md`, `0007-async-extraction-with-polling.md`
