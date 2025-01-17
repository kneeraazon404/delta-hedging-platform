# app/models/position.py
from datetime import datetime
from typing import Dict, List, Optional, Union

from .enums import OptionType, OrderDirection
from .hedge_record import HedgeRecord


class Position:
    def __init__(self, data: Dict):
        """Initialize position with market and option data"""
        # Handle both string and dict input
        if isinstance(data, str):
            raise ValueError("Position data must be a dictionary")

        # Validate and set required fields
        self.epic = data.get("epic") or data.get("underlying_epic")
        if not self.epic:
            raise ValueError("Epic is required but was not provided in data")

        # Basic position information
        self.strike = float(data.get("strike", 0))
        option_type_raw = data.get("option_type", "CALL")
        self.option_type = OptionType(
            option_type_raw.upper() if isinstance(option_type_raw, str) else "CALL"
        )
        self.direction = data.get("direction", "SELL")
        self.contract_size = float(data.get("contract_size", 1.0))
        self.size = float(data.get("size", data.get("contracts", 0)))

        # Price and value information
        self.level = float(data.get("level", 0))
        self.premium = float(
            data.get("premium", self.level * self.size * self.contract_size)
        )

        # Market information
        self.underlying_epic = data.get("underlying_epic", self.epic)
        self.last_market_data: Optional[Dict] = None
        self.last_update: Optional[datetime] = None

        # Time information
        self.expiry = data.get("expiry")
        self.time_to_expiry = float(data.get("time_to_expiry", 0.25))
        self.created_at = datetime.now().isoformat()

        # Hedging state
        self.hedge_size = float(data.get("hedge_size", 0.0))
        self.hedge_deal_id: Optional[str] = data.get("hedge_deal_id")
        self.last_hedge_price: Optional[float] = data.get("last_hedge_price")
        self.last_hedge_time: Optional[str] = data.get("last_hedge_time")
        self.hedge_history: List[HedgeRecord] = []
        self.strike = float(data.get("strike", 0.0))
        if self.strike <= 0:
            self.strike = float(data.get("market_data", {}).get("strike", 1.0))
        # Position state
        self.is_active = bool(data.get("is_active", True))
        self.deal_id = data.get("deal_id")
        self.pnl_threshold_crossed = bool(data.get("pnl_threshold_crossed", False))

    @classmethod
    def from_dict(cls, data: Dict) -> "Position":
        """Create Position from IG API response data"""
        try:
            position_data = data.get("position", {})
            market_data = data.get("market", {})

            if not position_data or not market_data:
                raise ValueError("Invalid position data structure")

            processed_data = {
                "epic": market_data.get("epic"),
                "strike": market_data.get("strike", 0),
                "option_type": market_data.get("instrumentName", "CALL").split()[-1],
                "direction": position_data.get("direction", "SELL"),
                "contract_size": position_data.get("contractSize", 1.0),
                "size": position_data.get("size", 0),
                "level": position_data.get("level", 0),
                "expiry": market_data.get("expiry"),
                "deal_id": position_data.get("dealId"),
                "time_to_expiry": cls._calculate_time_to_expiry(
                    market_data.get("expiry")
                ),
            }

            # Validate required fields
            if not processed_data["epic"]:
                raise ValueError("Missing epic in market data")

            return cls(processed_data)
        except Exception as e:
            raise ValueError(f"Error creating Position from dict: {str(e)}")

    @classmethod
    def from_json(cls, json_str: str) -> "Position":
        """Create Position from JSON string"""
        import json

        try:
            data = json.loads(json_str)
            return cls(data)
        except Exception as e:
            raise ValueError(f"Error creating Position from JSON: {str(e)}")

    def update_market_data(self, market_data: Dict) -> None:
        """Update position with latest market data"""
        if not isinstance(market_data, dict):
            raise ValueError("Market data must be a dictionary")
        self.last_market_data = market_data
        self.last_update = datetime.now()

    def calculate_intrinsic_value(self, current_price: float) -> float:
        """Calculate intrinsic value of the option"""
        try:
            current_price = float(current_price)
            if self.option_type == OptionType.CALL:
                return max(0, current_price - self.strike)
            return max(0, self.strike - current_price)
        except (ValueError, TypeError):
            raise ValueError("Invalid current price for intrinsic value calculation")

    def update_hedge(self, deal_id: str, size: float, price: float) -> None:
        """Update hedge position details"""
        try:
            self.hedge_deal_id = str(deal_id)
            self.hedge_size = float(size)
            self.last_hedge_price = float(price)
            self.last_hedge_time = datetime.now().isoformat()
            self.pnl_threshold_crossed = True
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid hedge update data: {str(e)}")

    def add_hedge_record(
        self, delta: float, hedge_size: float, price: float, pnl: float
    ) -> None:
        """Add a new hedge record"""
        try:
            record = HedgeRecord(
                float(delta), float(hedge_size), float(price), float(pnl)
            )
            self.hedge_history.append(record)
            self.last_hedge_time = record.timestamp
            self.last_hedge_price = price
            self.hedge_size = hedge_size
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid hedge record data: {str(e)}")

    def needs_hedge(self, current_pnl: float) -> bool:
        """Check if position needs hedging based on PnL threshold"""
        try:
            current_pnl = float(current_pnl)
            if not self.is_active or self.direction != "SELL":
                return False

            hedge_threshold = -self.premium
            # Need hedge if PnL below threshold and not already hedged
            if current_pnl <= hedge_threshold and not self.pnl_threshold_crossed:
                return True
            # Need to remove hedge if PnL improved above threshold
            if current_pnl > hedge_threshold and self.pnl_threshold_crossed:
                return True
            return False
        except (ValueError, TypeError):
            raise ValueError("Invalid PnL value for hedge check")

    def to_dict(self) -> Dict:
        """Convert position to dictionary representation"""
        try:
            return {
                "epic": self.epic,
                "underlying_epic": self.underlying_epic,
                "strike": self.strike,
                "option_type": self.option_type.value,
                "direction": self.direction,
                "contract_size": self.contract_size,
                "size": self.size,
                "premium": self.premium,
                "level": self.level,
                "time_to_expiry": self.time_to_expiry,
                "expiry": self.expiry,
                "created_at": self.created_at,
                "hedge_size": self.hedge_size,
                "hedge_deal_id": self.hedge_deal_id,
                "last_hedge_time": self.last_hedge_time,
                "last_hedge_price": self.last_hedge_price,
                "is_active": self.is_active,
                "deal_id": self.deal_id,
                "pnl_threshold_crossed": self.pnl_threshold_crossed,
                "total_hedges": len(self.hedge_history),
                "last_update": (
                    self.last_update.isoformat() if self.last_update else None
                ),
            }
        except Exception as e:
            raise ValueError(f"Error converting position to dict: {str(e)}")

    @staticmethod
    def _calculate_time_to_expiry(expiry_str: Optional[str]) -> float:
        """Calculate time to expiry in years"""
        if not expiry_str:
            return 0.25  # Default to 3 months
        try:
            expiry_date = datetime.strptime(expiry_str, "%d-%b-%y")
            days_to_expiry = (expiry_date - datetime.now()).days
            return max(
                days_to_expiry / 365, 0.0001
            )  # Minimum value to avoid division by zero
        except ValueError:
            return 0.25  # Default if parsing fails
