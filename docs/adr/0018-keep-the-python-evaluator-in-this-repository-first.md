# Keep the Python evaluator in this repository first

The first Nautilus evaluator will live in an independent Python-side directory in this repository. Keeping the collector and evaluator together while separating them by dataset contract makes schema changes, fixtures, and cross-language tests easier during early iteration.
