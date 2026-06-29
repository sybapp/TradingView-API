# Use session-based walk-forward windows

Walk-forward validation will expose training, scoring, and step sizes in complete RTH sessions rather than raw bar counts. This keeps validation aligned with intraday strategy boundaries, flat-before-close behavior, and overnight gaps, while still allowing lower-level code to count bars inside each resolved session window.
