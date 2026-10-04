# 0017 — Personal data is kept out by the schema and redacted by code, not by hand or by an agent

**Status:** Accepted (2026-10-04), revised the same day in F03

## Context
Receipts carry personal data that Budgie doesn't need: store addresses, masked card digits, terminal and transaction ids, cashier names, URLs. In the [AI spike](../backend/ai-spike.md) the model copied some of it into its output: payment lines read as items, and whole receipt lines in the free-text `unreadable_fields` of the base schema.

F03 first built its test fixtures by having a general-purpose agent anonymise the spike outputs by hand. That was rejected: in production there is no agent to anonymise receipts, and a hand edit can't be repeated or checked. If the app needs anonymisation, it has to be code that the app and the tooling share.

The first version of that code was a broad regex table: addresses, URLs, phone numbers, tax ids, cashier names, cards, ids. The review found that one rule hung on a line of `*` (catastrophic backtracking), and that the address rules redacted item names (`Hering 2` → `[address]`). Most of those rules only guarded against a free-text field the schema no longer has.

## Decision
- **First line of defence: the schema** ([ai-extraction](../backend/ai-extraction.md)).
  - The extraction schema has fixed keys and types. `unreadable_fields` and `payment_method` (`cash`, `card`, `voucher`, `other` or `null`) are enums, and no field asks for card, account or address data.
  - The prompt tells the model never to copy card numbers, IBANs, terminal, transaction or receipt numbers, addresses or staff names into any field.
- **Safety net: `backend/app/domain/redaction.py`**, pure and deterministic ([domain-logic](../backend/domain-logic.md#redactionpy-f3)). It covers only the three places where the model still writes free text:

  | Field | Handling |
  |---|---|
  | item descriptions | `redact_text`: IBAN, card number (Luhn), masked card digits, labelled ids |
  | `merchant` | `clean_merchant`: the first of up to three non-blank lines that keeps text after the cut at a postcode, phone number, URL or `@`; then `redact_text` |
  | raw model output | `redact_text` |

- **Production:** the pipeline (F05) applies these before storing. Raw model output, prompts and image bytes are never logged.
- **Fixtures:**
  - `scripts/make_fixtures.py` generates `tests/fixtures/recorded_responses/` deterministically. It reads only strict-schema spike outputs and makes every other case synthetic.
  - It refuses to write if a rule still matches or the independent scan flags a text.
  - The independent, broader scan (`scripts/fixture_scan.py`) runs in the generator and in pytest on every committed fixture.
- **Evaluation (F11):** committed evaluation material goes through the same module.

## Consequences
- The schema removes most leaks at the source. The small rule set is cheap, precise, runs in linear time (guarded by a timing test), and doesn't touch item names.
- A rule that is missing is added once and tested once, for the database, the logs, the fixtures and the evaluation alike.
- **Known gaps:**
  - header text the model copies into `merchant` stays in the stored raw output, unless F05 also replaces it there
  - the part of an e-mail address before the `@` can survive in `merchant`
  - a label glued into a rejected value isn't seen (`Kasse-Bon 1234`)
- If a future schema field adds free text, this decision has to be revisited for that field.
- The uploaded image is still stored unredacted. It stays local and is deleted with its receipt ([persistence](../backend/persistence.md)).
- The stored raw output is no longer exactly what the model said. The placeholders show where text was removed.
