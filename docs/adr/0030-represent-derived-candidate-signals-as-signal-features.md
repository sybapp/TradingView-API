# Represent derived candidate signals as signal features

Derived Candidate Signals are represented in `features.json` as `type: "signal"` records with stable signal names and `value: true` for simple Strategy Spec matching. Provenance and derivation details belong in optional feature metadata rather than in the matched value, so strategies can consume stable signals while experiments remain auditable.
