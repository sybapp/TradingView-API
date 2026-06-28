# Use next-bar execution for bar-level backtests

The first bar-level NautilusTrader validation loop will treat indicator and structural features as available only after the source bar closes. Orders generated from those features may execute no earlier than the next bar in the same RTH session, with fixed fees and slippage applied, to reduce lookahead bias in TradingView-derived signals. Signals whose next execution bar would cross a session boundary or fall after the flat-before-close cutoff are discarded rather than carried overnight.
