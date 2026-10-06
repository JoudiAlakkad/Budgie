// Mirrors the `Category` Literal in backend/app/api/schemas.py, in the same order.
// Keep one `"name",` per line: tests/unit/test_frontend_static.py parses this list.
export const CATEGORIES = [
  "groceries.fresh",
  "groceries.staples",
  "snacks_sweets",
  "drinks",
  "alcohol",
  "tobacco",
  "household",
  "personal_care",
  "health",
  "eating_out",
  "transport",
  "clothing",
  "electronics",
  "other",
  "deposit",
  "discount",
];

/** A line item that has no category yet; not a `Category`, so it can't be sent. */
export const UNCATEGORIZED = "uncategorized";

export const CATEGORY_LABELS = {
  "groceries.fresh": "Groceries: fresh",
  "groceries.staples": "Groceries: staples",
  snacks_sweets: "Snacks and sweets",
  drinks: "Drinks",
  alcohol: "Alcohol",
  tobacco: "Tobacco",
  household: "Household",
  personal_care: "Personal care",
  health: "Health",
  eating_out: "Eating out",
  transport: "Transport",
  clothing: "Clothing",
  electronics: "Electronics",
  other: "Other",
  deposit: "Deposit (Pfand)",
  discount: "Discount",
  uncategorized: "Uncategorised",
};

export function categoryLabel(category) {
  return CATEGORY_LABELS[category] || category;
}
