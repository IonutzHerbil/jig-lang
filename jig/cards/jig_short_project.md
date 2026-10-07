# Project files

- `lib/<name>.manifest` lists everything library `lib.<name>` exports: its newtypes, enums, records and
  `declare`d functions with their effects. Import only those names (`from lib.payments import charge`); calling
  one uses its effects. Library newtypes are distinct from project newtypes: convert by unwrapping, e.g.
  `Cents(int(total))`.
- `.decisions/*.decision` are project rules. A `✓ type X = X(base)` line is enforced; follow the rest too.
- Use the project's existing functions and types (shown above) rather than re-implementing them.
