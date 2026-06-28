# Split TradingView collection from Nautilus evaluation

The system will keep TradingView collection in the JavaScript repository and put NautilusTrader validation in a Python-side evaluator. This preserves the existing TradingView websocket and indicator extraction capability while letting NautilusTrader own data conversion, strategy execution, walk-forward backtesting, and fitness scoring.
