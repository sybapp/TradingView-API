# Use five-minute bars as the primary validation interval

The first validation loop will use 5-minute bars as the primary interval and 15-minute bars as a secondary comparison interval. This gives the strategy search enough samples for intraday equity index futures while avoiding a first version dominated by one-minute data noise and execution assumptions.
