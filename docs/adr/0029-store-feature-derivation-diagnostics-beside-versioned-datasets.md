# Store feature derivation diagnostics beside versioned datasets

Feature derivation failures for LuxAlgo ICT/SMC should be recorded as a dataset companion artifact instead of stdout-only logs or strategy-consumable features. A versioned dataset may therefore include diagnostics that explain unresolved directions, invalid zone geometry, missing provenance, and derivation counts without polluting `features.json`.
