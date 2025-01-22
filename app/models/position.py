import logging
from datetime import datetime
from typing import Dict, List, Optional, Union

from .enums import OptionType, OrderDirection
from .hedge_record import HedgeRecord

logger = logging.getLogger(__name__)


class Position:
    def __init__(self, data: Dict):
        """Initialize position with market and option data"""
        if isinstance(data, str):
            raise ValueError("Position data must be a dictionary")

        pos = data.get("position", {})
        market = data.get("market", {})

        # Handle position ID/reference
        self.deal_id = pos.get("dealId") or data.get("deal_id")
        if not self.deal_id:
            raise ValueError("Deal ID is required")

        # Basic position information
        self.epic = market.get("epic") or data.get("epic")
        if not self.epic:
            raise ValueError("Epic is required")

        self.underlying_epic = "IX.D.SPTRD.IFS.IP"

        # Position details
        self.strike = float(data.get("strike", pos.get("level", 0)))
        self.size = float(pos.get("size", 0))
        self.direction = pos.get("direction", "SELL")
        self.contract_size = float(pos.get("contractSize", 1.0))
        self.level = float(pos.get("level", 0))
        self.currency = pos.get("currency", "GBP")

        # Market information
        self.instrument_name = market.get("instrumentName", "")
        self.bid = market.get("bid", 0)
        self.offer = market.get("offer", 0)
        self.high = market.get("high", 0)
        self.low = market.get("low", 0)

        # Option type determination
        option_type_raw = str(market.get("instrumentType", "CALL")).upper()
        if "PUT" in option_type_raw:
            self.option_type = OptionType.PUT
        else:
            self.option_type = OptionType.CALL

        # Time information
        self.expiry = market.get("expiry")
        self.time_to_expiry = self._calculate_time_to_expiry()
        self.created_at = datetime.now().isoformat()
        self.last_update: Optional[datetime] = None

        # Calculate position values
        self.total_size = self.size * self.contract_size
        self.current_value = self.total_size * (
            self.bid if self.direction == "SELL" else self.offer
        )
        self.entry_value = self.total_size * self.level
        self.premium = self.entry_value

        # Calculate P&L
        if self.direction == "BUY":
            self.unrealized_pnl = (
                (self.bid - self.level) * self.total_size if self.bid > 0 else 0
            )
        else:
            self.unrealized_pnl = (
                (self.level - self.offer) * self.total_size if self.offer > 0 else 0
            )

        # Hedging state
        self.hedge_size = float(data.get("hedge_size", 0.0))
        self.hedge_deal_id: Optional[str] = data.get("hedge_deal_id")
        self.hedge_direction: Optional[str] = data.get("hedge_direction")
        self.last_hedge_price: Optional[float] = data.get("last_hedge_price")
        self.last_hedge_time: Optional[str] = data.get("last_hedge_time")
        self.hedge_history: List[HedgeRecord] = []
        self.pnl_threshold_crossed = bool(data.get("pnl_threshold_crossed", False))
        self.is_active = bool(data.get("is_active", True))

    def _validate_expiry(self) -> None:
        """Validate expiry format and value"""
        if self.expiry and isinstance(self.expiry, str):
            try:
                datetime.strptime(self.expiry, "%d-%b-%y")
            except ValueError:
                logger.warning(f"Invalid expiry format: {self.expiry}")
                self.expiry = None

    def _calculate_time_to_expiry(self) -> float:
        """Calculate time to expiry in years"""
        if not self.expiry:
            return 0.25

        try:
            if isinstance(self.expiry, str):
                expiry_date = datetime.strptime(self.expiry, "%d-%b-%y")
                days_to_expiry = (expiry_date - datetime.now()).days
                return max(days_to_expiry / 365.0, 0.001)
        except ValueError:
            logger.warning("Error calculating time to expiry")
            return 0.25

        return 0.25

    @classmethod
    def from_dict(cls, data: Dict) -> "Position":
        """Create Position from IG API response data"""
        try:
            position_data = data.get("position", {})
            market_data = data.get("market", {})

            if not position_data or not market_data:
                raise ValueError("Invalid position data structure")

            processed_data = {"position": position_data, "market": market_data}

            return cls(processed_data)

        except Exception as e:
            logger.error(f"Error creating Position from dict: {str(e)}")
            raise ValueError(f"Error creating Position from dict: {str(e)}")

    def update_market_data(self, market_data: Dict) -> None:
        """Update position with latest market data"""
        if not isinstance(market_data, dict):
            raise ValueError("Market data must be a dictionary")

        self.bid = market_data.get("bid", self.bid)
        self.offer = market_data.get("offer", self.offer)
        self.high = market_data.get("high", self.high)
        self.low = market_data.get("low", self.low)
        self.last_update = datetime.now()

        # Recalculate values
        self.current_value = self.total_size * (
            self.bid if self.direction == "SELL" else self.offer
        )
        if self.direction == "BUY":
            self.unrealized_pnl = (
                (self.bid - self.level) * self.total_size if self.bid > 0 else 0
            )
        else:
            self.unrealized_pnl = (
                (self.level - self.offer) * self.total_size if self.offer > 0 else 0
            )

    def calculate_intrinsic_value(self, current_price: float) -> float:
        """Calculate intrinsic value of the option"""
        try:
            if self.option_type == OptionType.CALL:
                return max(0, current_price - self.strike)
            return max(0, self.strike - current_price)
        except (ValueError, TypeError):
            raise ValueError("Invalid price for intrinsic value calculation")

    def update_hedge(
        self, deal_id: str, size: float, price: float, direction: str
    ) -> None:
        """Update hedge position details"""
        try:
            self.hedge_deal_id = str(deal_id)
            self.hedge_size = float(size)
            self.last_hedge_price = float(price)
            self.hedge_direction = str(direction)
            self.last_hedge_time = datetime.now().isoformat()
            self.pnl_threshold_crossed = True

            hedge_record = HedgeRecord(delta=0.0, hedge_size=size, price=price, pnl=0.0)
            self.hedge_history.append(hedge_record)

        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid hedge update data: {str(e)}")

    def add_hedge_record(
        self, delta: float, hedge_size: float, price: float, pnl: float
    ) -> None:
        """Add a new hedge record"""
        try:
            record = HedgeRecord(
                delta=float(delta),
                hedge_size=float(hedge_size),
                price=float(price),
                pnl=float(pnl),
            )
            self.hedge_history.append(record)
            self.last_hedge_time = record.timestamp
            self.last_hedge_price = price
            self.hedge_size = hedge_size
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid hedge record data: {str(e)}")

    def needs_hedge(self, current_pnl: float, threshold: float) -> bool:
        """Check if position needs hedging based on PnL threshold"""
        try:
            if not self.is_active or self.direction != "SELL":
                return False

            return (
                current_pnl <= -abs(threshold) and not self.pnl_threshold_crossed
            ) or (current_pnl > -abs(threshold) and self.pnl_threshold_crossed)
        except (ValueError, TypeError):
            raise ValueError("Invalid PnL value for hedge check")

    def to_dict(self) -> Dict:
        """Convert position to dictionary representation"""
        try:
            return {
                "deal_id": self.deal_id,
                "epic": self.epic,
                "underlying_epic": self.underlying_epic,
                "strike": self.strike,
                "option_type": self.option_type.value,
                "direction": self.direction,
                "contract_size": self.contract_size,
                "size": self.size,
                "premium": round(self.premium, 2),
                "level": self.level,
                "bid": self.bid,
                "offer": self.offer,
                "instrument_name": self.instrument_name,
                "currency": self.currency,
                "time_to_expiry": self.time_to_expiry,
                "expiry": self.expiry,
                "created_at": self.created_at,
                "total_size": self.total_size,
                "current_value": round(self.current_value, 2),
                "entry_value": round(self.entry_value, 2),
                "unrealized_pnl": round(self.unrealized_pnl, 2),
                "hedge_size": self.hedge_size,
                "hedge_deal_id": self.hedge_deal_id,
                "hedge_direction": self.hedge_direction,
                "last_hedge_time": self.last_hedge_time,
                "last_hedge_price": self.last_hedge_price,
                "is_active": self.is_active,
                "pnl_threshold_crossed": self.pnl_threshold_crossed,
                "total_hedges": len(self.hedge_history),
                "last_update": (
                    self.last_update.isoformat() if self.last_update else None
                ),
            }
        except Exception as e:
            raise ValueError(f"Error converting position to dict: {str(e)}")
