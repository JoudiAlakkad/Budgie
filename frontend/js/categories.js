// Mirrors the `Category` Literal in backend/app/api/schemas.py, in the same order.
// Keep one `"name",` per line: tests/unit/test_frontend_static.py parses this list.
export const CATEGORIES = [
  "groceries",
  "snacks_sweets",
  "drinks",
  "tobacco",
  "household",
  "personal_care",
  "health",
  "eating_out",
  "transport",
  "clothing",
  "electronics",
  "pet",
  "other",
  "deposit",
  "discount",
];

/** The contract's `SpendingCategory`: every category except `deposit` and `discount`. */
export const SPENDING_CATEGORIES = CATEGORIES.filter(
  (category) => category !== "deposit" && category !== "discount",
);

/** A line item that has no category yet; not a `Category`, so it can't be sent. */
export const UNCATEGORIZED = "uncategorized";

export const CATEGORY_LABELS = {
  groceries: "Groceries",
  snacks_sweets: "Snacks and sweets",
  drinks: "Drinks",
  tobacco: "Tobacco",
  household: "Household",
  personal_care: "Personal care",
  health: "Health",
  eating_out: "Eating out",
  transport: "Transport",
  clothing: "Clothing",
  electronics: "Electronics",
  pet: "Pet supplies",
  other: "Other",
  deposit: "Deposit (Pfand)",
  discount: "Discount",
  uncategorized: "Uncategorised",
};

export function categoryLabel(category) {
  return CATEGORY_LABELS[category] || category;
}
