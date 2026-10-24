# Contributing

Keep the product promise in `docs/product-boundaries.md` intact. New source adapters need a
declared exporter/engine version, units/ownership semantics, and positive/negative/missing-data
tests. Do not infer request timings from service aggregates or execution edges from span parents.

Use Python 3.12+, run `uv sync --locked`, then the lint/format/mypy/pytest checks listed in README.
Normal tests must not use the network, paid models, production services or a GPU. Live tests must
require explicit opt-in and preserve token/time/cost receipts. Sanitize any real fixtures and
document their provenance and redistribution permission before contributing them.

Changes affecting graph math need a small independent hand-calculated answer and property tests.
Changes affecting usage need duplicate/retry/missing-value cases. Do not turn unknowns into zero
to make a dashboard look complete. Keep model hypotheses outside deterministic facts.

Generated schemas/examples can be refreshed with `uv run python scripts/export_examples.py`;
existing native evidence example directories are not overwritten automatically.
