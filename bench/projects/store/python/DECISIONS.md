# Decisions

## money

- Context: Prices, totals, discounts, tax and refunds all handle money
- Decision: Money is an int number of cents, never float; percentages round down to whole cents via store.pricing.percent_of
- Rationale: Float arithmetic is inexact, and one rounding rule keeps every total reproducible
- Enforcement: Money is an int; the payments library takes Cents, converted with Cents(money)
