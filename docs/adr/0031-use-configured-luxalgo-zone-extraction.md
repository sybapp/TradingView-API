# Use configured LuxAlgo zone extraction

LuxAlgo ICT/SMC boxes can arrive from TradingView as anonymous `box_*` graphics with empty text, so name-based parsing is not enough to classify FVG and order-block liquidity zones. We will collect LuxAlgo with explicit zone-related inputs, enable Fair Value Gaps, extend FVG boxes beyond the default, and classify anonymous boxes using the configured color mapping before any zone-retest strategy relies on them.
