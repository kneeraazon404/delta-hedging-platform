# app/models/hedge_record.py
from datetime import datetime
from typing import Dict


class HedgeRecord:
    def __init__(self, delta: float, hedge_size: float, price: float, pnl: float):
        self.timestamp = datetime.now().isoformat()
        self.delta = delta
        self.hedge_size = hedge_size
        self.price = price
        self.pnl = pnl

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "delta": self.delta,
            "hedge_size": self.hedge_size,
            "price": self.price,
            "pnl": self.pnl,
        }
