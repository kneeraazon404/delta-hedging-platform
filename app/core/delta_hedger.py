# app/core/delta_hedger.py
import logging
from datetime import datetime
from typing import Dict, Optional

from app.core.option_calculator import OptionCalculator
from app.models.enums import OptionType, OrderDirection, OrderType
from app.models.hedge_record import HedgeRecord
from app.models.position import Position
from app.services.ig_client import IGClient
from config.settings import HEDGE_SETTINGS as _hedge_settings

logger = logging.getLogger(__name__)


class DeltaHedger:
    def __init__(self, ig_client: IGClient):
        self.ig_client = ig_client
        self.calculator = OptionCalculator()
        self.positions: Dict[str, Position] = {}
        self.monitoring_active = False
        self.last_check_time: Optional[datetime] = None

        # Load settings
        self.min_hedge_size = _hedge_settings["min_hedge_size"]
        self.max_hedge_size = _hedge_settings["max_hedge_size"]
        self.hedge_interval = _hedge_settings["hedge_interval"]
        self.delta_threshold = _hedge_settings["delta_threshold"]

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get position by ID with proper validation"""
        try:
            position = self.positions.get(position_id)
            if position:
                logger.info(f"Retrieved position {position_id}")
                return position

            # If not in local cache, check IG positions
            positions_data = self.ig_client.get_positions()
            if not positions_data or "positions" not in positions_data:
                logger.error("Failed to fetch positions data")
                return None

            # Look for the specific position
            for pos_data in positions_data["positions"]:
                if pos_data["position"]["dealId"] == position_id:
                    new_position = Position.from_dict(pos_data)
                    self.positions[position_id] = new_position
                    return new_position

            logger.warning(f"Position {position_id} not found")
            return None

        except Exception as e:
            logger.error(f"Error getting position {position_id}: {str(e)}")
            return None

    def calculate_position_delta(self, position: Position) -> Dict:
        """Calculate delta and hedging requirements"""
        try:
            if not position.epic:
                return {"error": "Position epic is None"}
            market_data = self.ig_client.get_market_data(position.epic)
            if not market_data:
                return {"error": "Failed to fetch market data"}

            current_price = (market_data["bid"] + market_data["offer"]) / 2
            volatility = market_data.get("volatility", 0.2)

            # Calculate Greeks
            greeks = self.calculator.calculate_greeks(
                S=current_price,
                K=position.strike,
                T=position.time_to_expiry,
                sigma=volatility,
                option_type=position.option_type,
            )

            # Calculate position delta
            position_delta = greeks["delta"] * position.size * position.contract_size
            current_hedge_delta = -position.hedge_size if position.hedge_size else 0
            net_delta = position_delta + current_hedge_delta

            # Determine if hedging is needed
            needs_hedge = abs(net_delta) > self.delta_threshold
            suggested_size = 0

            if needs_hedge:
                suggested_size = max(
                    min(abs(net_delta), self.max_hedge_size), self.min_hedge_size
                ) * (-1 if net_delta > 0 else 1)

            return {
                "current_price": current_price,
                "delta": net_delta,
                "position_delta": position_delta,
                "hedge_delta": current_hedge_delta,
                "needs_hedge": needs_hedge,
                "suggested_hedge_size": suggested_size,
                "greeks": greeks,
            }

        except Exception as e:
            logger.error(f"Error calculating delta: {str(e)}")
            return {"error": str(e)}

    def hedge_position(self, position_id: str, force_hedge: bool = False) -> Dict:
        """Execute hedging for a position"""
        try:
            position = self.get_position(position_id)
            if not position:
                return {"error": "Position not found"}

            delta_info = self.calculate_position_delta(position)
            if "error" in delta_info:
                return delta_info

            needs_hedge = force_hedge or delta_info["needs_hedge"]
            if not needs_hedge:
                return {"status": "no_action_needed", "delta": delta_info}

            # Execute hedge trade
            target_size = delta_info["suggested_hedge_size"]
            direction = OrderDirection.BUY if target_size > 0 else OrderDirection.SELL

            # Create hedge position
            if position.epic is None:
                return {"error": "Position epic is None"}

            hedge_result = self.ig_client.create_position(
                epic=position.epic,
                direction=direction,
                size=abs(target_size),
                order_type=OrderType.MARKET,
            )

            if hedge_result.get("dealId"):
                # Update position hedge state
                position.hedge_size = target_size
                position.hedge_deal_id = hedge_result["dealId"]

                # Record the hedge
                hedge_record = HedgeRecord(
                    delta=delta_info["delta"],
                    hedge_size=target_size,
                    price=delta_info["current_price"],
                    pnl=self.calculate_pnl(position, delta_info["current_price"]),
                )
                position.hedge_history.append(hedge_record)

                return {
                    "status": "hedged",
                    "deal_id": hedge_result["dealId"],
                    "hedge_size": target_size,
                    "delta": delta_info,
                    "hedge_record": hedge_record.to_dict(),
                }

            return {"error": "Hedge execution failed", "result": hedge_result}

        except Exception as e:
            logger.error(f"Error hedging position {position_id}: {str(e)}")
            return {"error": str(e)}

    def calculate_pnl(self, position: Position, current_price: float) -> float:
        """Calculate position PnL including hedges"""
        try:
            # Calculate option value
            intrinsic_value = position.calculate_intrinsic_value(current_price)
            option_pnl = position.premium - (
                intrinsic_value * position.size * position.contract_size
            )

            # Add hedge PnL if exists
            if position.hedge_size and position.last_hedge_price is not None:
                hedge_pnl = position.hedge_size * (
                    current_price - position.last_hedge_price
                )
                option_pnl += hedge_pnl

            return option_pnl

        except Exception as e:
            logger.error(f"Error calculating PnL: {str(e)}")
            raise

    def get_monitoring_status(self) -> Dict:
        """Get current monitoring status"""
        return {
            "active": self.monitoring_active,
            "last_check": (
                self.last_check_time.isoformat() if self.last_check_time else None
            ),
            "settings": {
                "min_hedge_size": self.min_hedge_size,
                "max_hedge_size": self.max_hedge_size,
                "hedge_interval": self.hedge_interval,
                "delta_threshold": self.delta_threshold,
            },
        }

    def get_position_status(self, position_id: str) -> Dict:
        """Get position status"""
        position = self.get_position(position_id)
        if not position:
            return {"error": "Position not found"}

        delta_info = self.calculate_position_delta(position)

        return {
            "position": position.to_dict(),
            "delta": delta_info,
            "hedge": {"size": position.hedge_size, "deal_id": position.hedge_deal_id},
            "hedge_history": [record.to_dict() for record in position.hedge_history],
        }

    def calculate_position_metrics(self, position: Position) -> Dict:
        """Calculate key metrics for a position including PnL and delta"""
        try:
            if not position.epic:
                return {"error": "Position epic is None"}

            # Get market data
            market_data = self.ig_client.get_market_data(position.epic)
            if not market_data:
                return {"error": "Failed to fetch market data"}

            current_price = (market_data["bid"] + market_data["offer"]) / 2

            # Calculate PnL
            pnl = self.calculate_pnl(position, current_price)

            # Get delta information
            delta_info = self.calculate_position_delta(position)

            return {
                "pnl": pnl,
                "current_price": current_price,
                "delta": delta_info.get("delta"),
                "needs_hedge": delta_info.get("needs_hedge", False),
                "hedge_size": position.hedge_size,
                "premium": position.premium,
            }

        except Exception as e:
            logger.error(f"Error calculating metrics: {str(e)}")
            return {"error": str(e)}

    def get_all_positions_status(self) -> Dict:
        """Get status for all tracked positions"""
        try:
            positions_status = {}
            for position_id, position in self.positions.items():
                metrics = self.calculate_position_metrics(position)
                positions_status[position_id] = {
                    "position": position.to_dict(),
                    "metrics": metrics,
                    "hedge_history": [
                        record.to_dict() for record in position.hedge_history
                    ],
                }

            return {"status": "success", "positions": positions_status}

        except Exception as e:
            logger.error(f"Error getting positions status: {str(e)}")
            return {"error": str(e)}

    def get_current_settings(self) -> Dict:
        """Get current hedger settings"""
        return {
            "min_hedge_size": self.min_hedge_size,
            "max_hedge_size": self.max_hedge_size,
            "hedge_interval": self.hedge_interval,
            "delta_threshold": self.delta_threshold,
        }

    def validate_settings(self, settings: Dict) -> Dict:
        """Validate and update hedger settings"""
        try:
            # Validate settings
            required_fields = [
                "min_hedge_size",
                "max_hedge_size",
                "hedge_interval",
                "delta_threshold",
            ]
            for field in required_fields:
                if field not in settings:
                    return {"error": f"Missing required field: {field}"}

            # Validate values
            if settings["min_hedge_size"] <= 0:
                return {"error": "min_hedge_size must be positive"}
            if settings["max_hedge_size"] <= settings["min_hedge_size"]:
                return {"error": "max_hedge_size must be greater than min_hedge_size"}
            if settings["hedge_interval"] <= 0:
                return {"error": "hedge_interval must be positive"}
            if settings["delta_threshold"] <= 0:
                return {"error": "delta_threshold must be positive"}

            # Update settings
            self.min_hedge_size = settings["min_hedge_size"]
            self.max_hedge_size = settings["max_hedge_size"]
            self.hedge_interval = settings["hedge_interval"]
            self.delta_threshold = settings["delta_threshold"]

            return {"status": "success", "settings": self.get_current_settings()}

        except Exception as e:
            logger.error(f"Error validating settings: {str(e)}")
            return {"error": str(e)}
