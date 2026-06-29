# Record every run in a local run registry

Every backtest and optimization run must write a local run record containing the dataset version, strategy spec, optimizer configuration, evaluator version, cost model, walk-forward windows, fitness breakdown, and result artifacts. A file-based run registry is enough for the first version and keeps high-Sharpe candidates reproducible.
