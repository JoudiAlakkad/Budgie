# 0027 — Fixes from the first v3 host test: copied examples, quantity lines, number items, the paid total, upload size

**Status:** Accepted (2026-10-11)

## Context
The student ran v3 (`gemma3:4b`) on real receipts after F13 (2026-10-11). The stored raw output showed:
- **Copied examples.** A Lidl receipt without any deposit got `PFAND -0.25` and `LEERGUT -0.75` as its last items. These are the v3 text example (`Pfand -0,25`) and the example answer's Leergut (−0.75).
- **Quantity lines as items.** On an ALDI receipt the model listed `24 x 0,49 €` (11.76) and `RHNEIFELS WASSER M. AR` (0.25) as two items, and `3 x 0,89 €` and `-53 x 0,25 €` the same way.
- **A number as an item and as the total.** `219,000` → 219.00 was stored as an item and also became `total`, while the receipt says `ZU ZAHLEN 54,40`. The model listed that line as an item and the guard dropped it, so the review page said "Items sum to 261.93 but total is 219.00".
- **A Pfand charge negated.** ALDI prints the deposit charge as `PFAND / RÜCKGABE 0,25` (6.00 for 24 bottles). The bare word `rueckgabe` made the code negate it to −6.00.
- **A 5.2 MB photo was refused** with a size message. The API's limit is 10 MB, and a 5.2 MB upload returns 202 in a test, so the API doesn't reject it. Phone photos are often 5–12 MB, and the model sees at most about 900 px anyway.

## Decision
The student asked for all of these to be fixed (2026-10-11). F13 isn't merged, so **v3 is changed in place**; v1 and v2 stay frozen.
- **v3 gets its own example answer** (`EXAMPLE_OUTPUT_V3`, `OUTPUT_SPECS["v3"]`) **without any Pfand or Leergut line**, and the Pfand section of `system.txt` drops its concrete example lines. It now says: list a Pfand or Leergut item only if that line is printed on this receipt; most receipts have none. The sign rule (printed sign first) stays. v3's prompt also says that a line with only a number is never an item, and that the total is the amount on the ZU ZAHLEN / SUMME line.
- **No-letter lines (code):** an item whose description has no letter at all (`219,000`, `4711`, `-`) is a non-item and is dropped.
- **Quantity lines (code):** an item whose description is only a quantity line (`24 x 0,49 €`, `3 x 0,89`, `1,066 kg x 0,99 €/kg`, `-53 x 0,25 €`) is merged into a neighbour:
  - The neighbour is the next item, or the previous one if the next one doesn't qualify. It must have a name (it isn't itself a quantity line) and must not be a deposit item, unless the quantity line is negative (a return like `-53 x 0,25 €` merges into a following Leergut line).
  - The merged item keeps the neighbour's description, takes the quantity N as its `qty` (the absolute value), and takes the quantity line's amount when it equals N × price within 0.02. Otherwise it keeps the neighbour's amount.
  - Without a qualifying neighbour the quantity line stays an item, so the user sees and names it.
- **The paid total (code):** if the model listed a `zu zahlen` or `gesamtbetrag` line as an item (the guard drops it), and `total` is null or differs from that line's amount by more than 0.02, the line's amount becomes the total. This happens only when those lines agree with each other; `summe` doesn't count, because tax tables print one too. It runs before the facts are built, so the sum check compares against the paid amount.
- **Return words:** a bare `rueckgabe` is no longer a return word. `Pfand / Rückgabe` and `Pfand-Rückgabe` (two words after folding) are treated as a plain Pfand: never negated, only flagged. Single-word `pfandrueckgabe`, `leergutrueckgabe`, `flaschenrueckgabe`, `leergut`, `leergutbon`, `pfandbon`, `pfand zurueck` and `zurueck pfand` stay return words.
- **Upload (frontend):** before upload the page downsizes a photo larger than 2 MB, or one whose long side is over 2,000 px, to a JPEG with the long side at most 2,000 px (quality 0.85), keeping EXIF orientation. If that fails (an unsupported browser or a decode error), it uploads the original. The 413 message names the limit, and the page says so before uploading a file still over 10 MB.

## Consequences
- The guard now changes items in three ways (drop, merge, negate) plus the total. Each change is a logged count through a fixed template, and the raw output keeps the model's answer for F11.
- A merge can attach a quantity line to the wrong product when the model has also lost the order. The sum check and the photo remain the user's check.
- The uploaded image is the downsized one, so the review page shows that. 2,000 px is enough to read a receipt.
- The cause of the "too big" message is still unknown, since the API didn't produce it. If it shows again after the downsizing, the exact text will tell where it comes from.
- Amends [0026](0026-prompt-v3-non-item-guard-and-pfand-sign.md) (return words, v3's example).
