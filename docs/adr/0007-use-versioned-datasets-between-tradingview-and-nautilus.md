# Use versioned datasets between TradingView and NautilusTrader

TradingView collection will write immutable datasets before NautilusTrader backtests consume them. Keeping collection separate from validation makes experiments reproducible, avoids live TradingView availability or login state affecting backtests, and gives every strategy result a concrete data version.
