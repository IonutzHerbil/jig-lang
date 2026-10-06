# Mistakes models often make in Jig

Learned from 146 benchmark samples; avoid these.

- There is no `while`. Loop with `for _ in range(LIMIT):` and `break`, or `for` over the data. (seen in 23% of first attempts)
- A function with `requires:` needs an example `f(<bad input>) -> rejected`. (seen in 6% of first attempts)
- A function returning Result needs an example that returns Err from the body. A `requires` refusal is `-> rejected` and does not count. (seen in 4% of first attempts)
- These modules do not exist: `std.string`. Only std.ctx, std.option, std.record, std.result, project modules and `lib.*` manifests can be imported; strings, math and collections need no import. (seen in 3% of first attempts)
- No `raise` or `try`: return `Err(...)`, and propagate with `?`. (seen in 2% of first attempts)
