# app/models/position.py

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

        # Handle position ID/reference
        self.deal_id = data.get("dealId") or data.get("deal_id")
        if not self.deal_id:
            raise ValueError("Deal ID is required")

        # Basic position information
        self.epic = data.get("epic") or data.get("market", {}).get("epic")
        if not self.epic:
            raise ValueError("Epic is required")

        self.underlying_epic = "IX.D.SPTRD.IFS.IP"

        # Position details
        self.strike = float(data.get("strike", data.get("level", 0)))

        # Handle option type
        option_type_raw = str(
            data.get(
                "option_type", data.get("market", {}).get("instrumentType", "CALL")
            )
        ).upper()
        try:
            if "PUT" in option_type_raw:
                self.option_type = OptionType.PUT
            else:
                self.option_type = OptionType.CALL
        except ValueError:
            logger.warning(
                f"Invalid option type '{option_type_raw}', defaulting to CALL"
            )
            self.option_type = OptionType.CALL

        # Direction and size
        self.direction = data.get("direction", "SELL")
        self.contract_size = float(
            data.get("contract_size", data.get("contractSize", 1.0))
        )
        self.size = float(data.get("size", 0))

        # Price and value information
        self.level = float(data.get("level", 0))
        self.premium = float(
            data.get("premium", self.level * self.size * self.contract_size)
        )

        # Market information
        self.market_name = data.get("marketName", "")
        self.instrument_type = data.get("instrumentType", "")
        self.currency = data.get("currency", "GBP")
        self.last_market_data: Optional[Dict] = None
        self.last_update: Optional[datetime] = None

        # Time information
        self.expiry = data.get("expiry")
        self._validate_expiry()
        self.time_to_expiry = self._calculate_time_to_expiry()
        self.created_at = datetime.now().isoformat()

        # Hedging state
        self.hedge_size = float(data.get("hedge_size", 0.0))
        self.hedge_deal_id: Optional[str] = data.get("hedge_deal_id")
        self.hedge_direction: Optional[str] = None
        self.last_hedge_price: Optional[float] = data.get("last_hedge_price")
        self.last_hedge_time: Optional[str] = data.get("last_hedge_time")
        self.hedge_history: List[HedgeRecord] = []
        self.pnl_threshold_crossed = bool(data.get("pnl_threshold_crossed", False))

        # Position state
        self.is_active = bool(data.get("is_active", True))

    def _validate_expiry(self) -> None:
        """Validate expiry format and value"""
        if self.expiry:
            try:
                if isinstance(self.expiry, str):
                    datetime.strptime(self.expiry, "%d-%b-%y")
            except ValueError:
                logger.warning(f"Invalid expiry format: {self.expiry}, setting to None")
                self.expiry = None

    def _calculate_time_to_expiry(self) -> float:
        """Calculate time to expiry in years"""
        if not self.expiry:
            return 0.25  # Default to 3 months

        try:
            if isinstance(self.expiry, str):
                expiry_date = datetime.strptime(self.expiry, "%d-%b-%y")
                days_to_expiry = (expiry_date - datetime.now()).days
                # Ensure minimum value to avoid division by zero in calculations
                return max(days_to_expiry / 365.0, 0.001)
        except ValueError:
            logger.warning("Error calculating time to expiry, using default")
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

            processed_data = {
                "dealId": position_data.get("dealId"),
                "epic": market_data.get("epic"),
                "strike": float(
                    market_data.get("strike", 0) or position_data.get("level", 0)
                ),
                "direction": position_data.get("direction", "SELL"),
                "contract_size": float(position_data.get("contractSize", 1.0)),
                "size": float(position_data.get("size", 0)),
                "level": float(position_data.get("level", 0)),
                "expiry": market_data.get("expiry"),
                "marketName": market_data.get("instrumentName", ""),
                "instrumentType": market_data.get("instrumentType", ""),
                "currency": position_data.get("currency", "GBP"),
            }

            return cls(processed_data)

        except Exception as e:
            logger.error(f"Error creating Position from dict: {str(e)}")
            raise ValueError(f"Error creating Position from dict: {str(e)}")

    def update_market_data(self, market_data: Dict) -> None:
        """Update position with latest market data"""
        if not isinstance(market_data, dict):
            raise ValueError("Market data must be a dictionary")

        self.last_market_data = market_data
        self.last_update = datetime.now()

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

            # Add hedge record
            hedge_record = HedgeRecord(
                delta=0.0,  # This will be updated with actual delta
                hedge_size=size,
                price=price,
                pnl=0.0,  # This will be updated with actual PnL
            )
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

    def needs_hedge(self, current_pnl: float) -> bool:
        """Check if position needs hedging based on PnL threshold"""
        try:
            if not self.is_active or self.direction != "SELL":
                return False

            hedge_threshold = -abs(self.premium)
            return (
                current_pnl <= hedge_threshold and not self.pnl_threshold_crossed
            ) or (current_pnl > hedge_threshold and self.pnl_threshold_crossed)
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
                "premium": self.premium,
                "level": self.level,
                "market_name": self.market_name,
                "instrument_type": self.instrument_type,
                "currency": self.currency,
                "time_to_expiry": self.time_to_expiry,
                "expiry": self.expiry,
                "created_at": self.created_at,
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
