# Use multi-constraint fitness instead of Sharpe-only ranking

The optimizer will not rank candidates by raw Sharpe alone. A candidate must first survive constraints such as minimum trade count, maximum drawdown, fee and slippage assumptions, and robustness across market regimes; out-of-sample Sharpe can then be used as the primary ranking signal among survivors. If no candidate survives, the search records a completed run with no winner rather than promoting the best rejected candidate.
