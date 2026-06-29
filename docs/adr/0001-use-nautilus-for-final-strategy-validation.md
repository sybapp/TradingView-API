# Use NautilusTrader for final strategy validation

TradingView-derived bars, indicator plots, graphics, drawings, and strategy reports may be used to generate candidate signals and features, but they are not the final performance authority. Final strategy scoring must happen inside NautilusTrader so the project evaluates candidates with one execution model, fee/slippage assumptions, order handling semantics, and performance calculation path.
