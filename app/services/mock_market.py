# app/services/mock_market.py
import time
from datetime import datetime
from typing import Dict

import numpy as np


class MockMarketData:
    def __init__(self):
        self.base_price = 1.2000
        self.volatility = 0.02
        self.last_update = time.time()
        self.update_interval = 1  # 1 second

    def get_price(self) -> float:
        current_time = time.time()
        time_diff = current_time - self.last_update

        if time_diff >= self.update_interval:
            random_walk = np.random.normal(0, self.volatility * np.sqrt(time_diff))
            self.base_price *= 1 + random_walk
            self.last_update = current_time

        return self.base_price

    def get_market_data(self) -> Dict:
        current_price = self.get_price()
        return {
            "price": current_price,
            "volatility": self.volatility,
            "high": current_price * 1.01,
            "low": current_price * 0.99,
            "timestamp": datetime.now().isoformat(),
            "instrument_type": "CURRENCIES",
            "expiry": "-",
            "lot_size": 1,
            "currency": "USD",
            "bid": current_price - 0.0001,
            "ask": current_price + 0.0001,
            "spread": 0.0002,
        }
