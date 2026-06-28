# Use next-bar execution for bar-level backtests

The first bar-level NautilusTrader validation loop will treat indicator and structural features as available only after the source bar closes. Orders generated from those features may execute no earlier than the next bar, with fixed fees and slippage applied, to reduce lookahead bias in TradingView-derived signals.
