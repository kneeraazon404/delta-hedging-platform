from datetime import datetime
from typing import Dict, List, Optional

from .enums import OptionType, OrderDirection
from .hedge_record import HedgeRecord

_positions = {}


class Position:
    def __init__(self, data: Dict):
        # Basic position information
        self.epic = data.get("epic") or data.get("underlying_epic")
        if not self.epic:
            raise ValueError("Epic is required but was not provided in data")

        self.strike = float(data["strike"])
        self.option_type = OptionType(data["option_type"].upper())
        self.direction = data.get("direction", "SELL")
        self.is_sold = self.direction == "SELL"

        # Size and premium information
        self.contracts = int(data.get("size", data.get("contracts", 0)))
        self.level = float(data.get("level", 0))
        self.premium = float(data.get("premium", self.level * self.contracts))

        # Underlying information
        self.underlying_epic = data.get(
            "underlying_epic", self.epic
        )  # Fallback to derived epic

        # Time information
        self.expiry = data.get("expiry")
        self.time_to_expiry = float(data.get("time_to_expiry", 0.25))
        self.created_at = datetime.now().isoformat()

        # Hedging state
        self.hedge_size = 0.0
        self.last_hedge_time = None
        self.hedge_history: List[HedgeRecord] = []
        self.hedge_deal_id: Optional[str] = None
        self.last_delta = 0.0

        # Position state
        self.is_active = True
        self.pnl_threshold_crossed = False
        self.deal_id = data.get("deal_id")
        self.last_market_data = None

    def to_dict(self) -> Dict:
        return {
            "epic": self.epic,
            "underlying_epic": self.underlying_epic,
            "strike": self.strike,
            "option_type": self.option_type.value,
            "direction": self.direction,
            "premium": self.premium,
            "contracts": self.contracts,
            "level": self.level,
            "time_to_expiry": self.time_to_expiry,
            "expiry": self.expiry,
            "created_at": self.created_at,
            "hedge_size": self.hedge_size,
            "last_hedge_time": self.last_hedge_time,
            "is_active": self.is_active,
            "pnl_threshold_crossed": self.pnl_threshold_crossed,
            "total_hedges": len(self.hedge_history),
            "deal_id": self.deal_id,
            "hedge_deal_id": self.hedge_deal_id,
            "last_delta": self.last_delta,
        }

    def add_hedge_record(
        self, delta: float, hedge_size: float, price: float, pnl: float
    ):
        """Add a new hedge record"""
        record = HedgeRecord(delta, hedge_size, price, pnl)
        self.hedge_history.append(record)
        self.last_hedge_time = record.timestamp
        self.hedge_size = hedge_size
        self.last_delta = delta

    def needs_hedge(self, current_pnl: float) -> bool:
        """Check if position needs hedging based on PnL threshold"""
        if not self.is_active or not self.is_sold:
            return False

        hedge_threshold = -self.premium

        # Need hedge if PnL below threshold and not already hedged
        if current_pnl <= hedge_threshold and not self.pnl_threshold_crossed:
            return True

        # Need to remove hedge if PnL improved above threshold
        if current_pnl > hedge_threshold and self.pnl_threshold_crossed:
            return True

        return False

    def get_hedge_direction(self, new_hedge_size: float) -> Optional[OrderDirection]:
        """Determine hedge direction based on current and new hedge sizes"""
        if not self.is_active or not self.is_sold:
            return None

        hedge_difference = new_hedge_size - self.hedge_size

        if abs(hedge_difference) < 0.01:  # Minimum adjustment threshold
            return None

        return OrderDirection.BUY if hedge_difference > 0 else OrderDirection.SELL

    def update_hedge_state(self, deal_id: Optional[str] = None):
        """Update hedging state"""
        if deal_id:
            self.hedge_deal_id = deal_id
            self.pnl_threshold_crossed = True
        else:
            self.hedge_deal_id = None
            self.pnl_threshold_crossed = False
            self.hedge_size = 0.0
            self.last_delta = 0.0

    def get_last_hedge(self) -> Optional[HedgeRecord]:
        """Get the most recent hedge record"""
        return self.hedge_history[-1] if self.hedge_history else None
