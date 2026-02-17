 example ──
    # (Real candles Delta API se fetch karo, yeh sirf demo data hai)
    sample_candles = [
        {"open": 29000, "high": 29500, "low": 28800, "close": 29200, "volume": 100},
        {"open": 29200, "high": 29800, "low": 29100, "close": 29600, "volume": 120},
        {"open": 29600, "high": 30200, "low": 29500, "close": 30000, "volume": 150},
        {"open": 30000, "high": 30800, "low": 29900, "close": 30500, "volume": 200},
        {"open": 30500, "high": 31000, "low": 29800, "close": 29900, "volume": 180},
    ]
    backtest(sample_candles, example_strategy)
