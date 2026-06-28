# Use a feature sidecar before Nautilus custom data

The first real NautilusTrader strategy adapter may load TradingView-derived features as a read-only sidecar timeline instead of modeling every feature as Nautilus custom data. The strategy must still execute inside NautilusTrader and may only query sidecar features during Nautilus-driven bar handling using `availabilityTime`, so the sidecar cannot become a lookahead channel; custom data streams can replace this once the engine path is stable.
