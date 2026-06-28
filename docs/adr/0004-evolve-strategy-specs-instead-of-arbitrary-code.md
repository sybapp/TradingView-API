# Evolve strategy specs instead of arbitrary code

The strategy evolution loop will search over constrained strategy specs rather than arbitrary generated Python code. A structured spec keeps candidates reproducible and comparable, gives the optimizer a bounded search space, and lets the system compile candidates into NautilusTrader strategies only after they pass schema validation.
