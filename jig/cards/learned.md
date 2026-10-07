# Mistakes models often make in Jig

Learned from 235 benchmark samples; avoid these.

- These modules do not exist or are not importable: `std.string`, `payments`. Import the pure standard library, project modules, `std.*` and `lib.*` manifests; time and randomness come from `ctx`. (seen in 3% of first attempts)
