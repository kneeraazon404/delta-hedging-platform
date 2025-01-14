# app/models/position.py
from datetime import datetime
from typing import Dict, List

from .enums import OptionType
from .hedge_record import HedgeRecord

_positions = {}


class Position:
    def __init__(self, data: Dict):
        self.epic = data["epic"]
        self.strike = float(data["strike"])
        self.option_type = OptionType(data["option_type"].upper())
        self.premium = float(data["premium"])
        self.contracts = int(data["contracts"])
        self.time_to_expiry = float(data["time_to_expiry"])
        self.created_at = datetime.now().isoformat()
        self.hedge_size = 0.0
        self.last_hedge_time = None
        self.hedge_history: List[HedgeRecord] = []
        self.is_active = True
        self.pnl_threshold_crossed = False
        self.deal_id = None
        self.last_market_data = None

    def to_dict(self) -> Dict:
        return {
            "epic": self.epic,
            "strike": self.strike,
            "option_type": self.option_type.value,
            "premium": self.premium,
            "contracts": self.contracts,
            "time_to_expiry": self.time_to_expiry,
            "created_at": self.created_at,
            "hedge_size": self.hedge_size,
            "last_hedge_time": self.last_hedge_time,
            "is_active": self.is_active,
            "pnl_threshold_crossed": self.pnl_threshold_crossed,
            "total_hedges": len(self.hedge_history),
            "deal_id": self.deal_id,
        }

    def add_hedge_record(
        self, delta: float, hedge_size: float, price: float, pnl: float
    ):
        record = HedgeRecord(delta, hedge_size, price, pnl)
        self.hedge_history.append(record)
        self.last_hedge_time = record.timestamp
        self.hedge_size = hedge_size
