# Start with a bar-level validation loop

The first version of the strategy evolution system will validate candidates on bar-level data in NautilusTrader. TradingView produces chart bars, indicator plots, and graphics naturally, so starting with Nautilus `Bar` data keeps the first loop focused on strategy iteration; tick or quote data can be added later as a stricter second validation layer.
