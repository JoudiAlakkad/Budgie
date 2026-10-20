# 0022 — Demo data by an explicit command through the services

**Status:** Accepted (2026-10-08)

## Context
Scenarios 2 and 3 must work "on the seeded demo data" ([roadmap](../plan/roadmap.md)). The roadmap put the demo seed in F9; the student moved it to F8, and F9 extends it with leak patterns. Data handling must be tested app code, not a one-off agent task.

## Decision
- **An explicit command**, `python -m app.demo_seed [--today YYYY-MM-DD]` (`make seed-demo`; in Docker `docker exec <ctr> python -m app.demo_seed`). There is no config flag: a startup side effect could load demo data into a real database.
- **It only loads into an empty database:** with any expense, receipt, budget or goal it refuses (exit 1) and changes nothing. `item_categories` doesn't count, since startup always seeds it.
- **Through the services:** each expense is created and confirmed with `ExpenseService`, and budgets and the goal are set with `BudgetService`. The stored data is thus normalised, categorised and assessed by the same rules as real data.
- **Relative to today:** the two previous months are full, and the current month holds the days up to today. The plan is deterministic for a given `today`; every description is in the item seed and the items sum to the total, so no expense is flagged.
- The logic is `services/demo_seed.py`, with tests; `app/demo_seed.py` is a thin CLI.

## Consequences
- The seed isn't atomic: it runs one transaction per expense. If it fails halfway, delete the database and run it again.
- The CLI's exit 2 means a storage failure, but argparse also exits 2 on a malformed `--today`.
- `over` (electronics) holds on every day and `on_pace_to_overrun` (health) on every day except the month's last, where projected = spent; a test checks every day of several months.
- **F09 leak patterns** ([0023](0023-leak-rules-and-thresholds.md)): the eating-out budget is 50 € with 41,00 € on day 1, so the eating-out burn card shows on every day of the first half; a 7,99 € wine purchase on day 1 makes alcohol spike against the two previous months (since [0025](0025-category-revision.md): Rotwein + Sekt, 19,98 €, make `drinks` spike). Side effects: health also burns in the first half, eating out turns over budget and then spike in the second half, and the goal card is Behind until day 3.
- On a storage failure or any other app error during the seed (exit 2), the CLI says the data may be partly loaded and how to recover. A programming error still shows a traceback.
- The emptiness check runs in its own transaction, before the inserts. While the app is running, a write between the check and the inserts mixes demo data into real data; seed a fresh database with the app stopped or idle.

## F12 addition (2026-10-20)
The seed covers scenarios 2 and 3. Scenario 1 needs a photo to upload, and the eval photos are private ([0028](0028-evaluation-truth-and-scoring.md)). So `scripts/make_demo_receipts.py` (`make demo-receipts`, tested in `tests/unit/test_make_demo_receipts.py`) renders three synthetic receipts into `data/demo/receipts/`, and they are committed:
- a supermarket with seed categories, a Pfand line and one unknown item
- a café whose known items (cappuccino, latte macchiato) are all eating out, plus one unknown item (KAESEKUCHEN), 10,90 in total. After the second `/code-review`, CROISSANT was replaced by LATTE MACCHIATO, because the seed maps croissant to groceries, which put a café purchase under groceries. A test checks that every known item on the café receipt maps to `eating_out`.
- a kiosk with only known items and CHIPS PAPRIKA again, so the category picked on the supermarket receipt is reused and nothing new needs a category; its printed total (5,29) is 1,00 more than the items (4,29), which gives a sum flag. After `/code-review`, HARIBO GOLDBAEREN was dropped: as a second unknown item it blocked confirming the reuse case. The test checks that every unknown item on the kiosk receipt also appears on the supermarket receipt.

Seed the history first, because `make seed-demo` only loads into an empty database, then upload the receipts in order.

Each committed PNG is compared pixel for pixel with the script's render, so a change to the receipt data needs `make demo-receipts` in the same commit. A newer Pillow 12.x could move glyph edges and fail this check with no change to the content; the failure message names the Pillow and FreeType versions (rendered with 12.3.0), and re-rendering fixes it. The check isn't loosened.

The shops are fictional and the receipts carry no address or payment data. The text is ASCII because Pillow's bundled font has no umlauts or euro sign. The printed dates are October 2026, so after October 2028 they fall outside the 2-year date rule and get flagged.
