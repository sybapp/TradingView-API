# Export versioned dataset directories

The TradingView collector will export versioned dataset directories rather than a single flat file. Each dataset must include a manifest for metadata, a bars file for OHLCV data, and feature files for indicator plots and structural features with event time, availability time, and repainting-risk information.
