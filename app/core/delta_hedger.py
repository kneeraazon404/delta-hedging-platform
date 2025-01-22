# app/core/delta_hedger.py
import json
import logging
import time
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
        """Calculate delta with improved error handling and edge case support"""
        try:
            # Handle extremely short time to expiry
            if position.time_to_expiry <= 0.001:
                logger.warning(f"Position near expiry: {position.deal_id}")

                # Determine intrinsic value based on current market price
                market_data = self.ig_client.get_market_data(position.epic)
                if not market_data:
                    logger.error("Failed to fetch market data for near-expiry position")
                    return {"error": "Failed to fetch market data"}

                current_price = float(market_data.get("price", 0))

                # Intrinsic value calculation for call and put
                if position.option_type == OptionType.CALL:
                    intrinsic_value = max(current_price - position.strike, 0)
                else:  # PUT
                    intrinsic_value = max(position.strike - current_price, 0)

                # If intrinsic value is zero, delta is zero
                delta = (
                    0 if intrinsic_value == 0 else (1 if intrinsic_value > 0 else -1)
                )

                return {
                    "current_price": current_price,
                    "delta": delta,
                    "position_delta": delta * position.size * position.contract_size,
                    "greeks": {
                        "delta": delta,
                        "gamma": 0,
                        "theta": 0,
                        "vega": 0,
                        "rho": 0,
                    },
                    "needs_hedge": False,  # Near expiry, likely to be settled soon
                }

            # Normal delta calculation
            market_data = self.ig_client.get_market_data(position.epic)
            if not market_data:
                return {"error": "Failed to fetch market data"}

            current_price = float(market_data.get("price", 0))
            if current_price <= 0:
                return {"error": "Invalid market price"}

            # Use existing volatility if available, otherwise use a default
            volatility = max(market_data.get("volatility", 0.2), 0.1)

            # Ensure reasonable time to expiry
            time_to_expiry = max(position.time_to_expiry, 0.001)

            # Calculate Greeks
            greeks = self.calculator.calculate_greeks(
                S=current_price,
                K=position.strike,
                T=time_to_expiry,
                sigma=volatility,
                option_type=position.option_type,
            )

            return {
                "current_price": current_price,
                "delta": greeks["delta"],
                "position_delta": greeks["delta"]
                * position.size
                * position.contract_size,
                "greeks": greeks,
                "needs_hedge": abs(greeks["delta"]) > self.delta_threshold,
            }

        except Exception as e:
            logger.error(f"Delta calculation error: {str(e)}")
            return {"error": str(e)}

    def hedge_position(self, position_id: str, hedge_size: float = None, force_hedge: bool = False) -> Dict:  # type: ignore
        """Execute hedging with CFD positions"""
        try:
            # Retrieve position
            position = self.get_position(position_id)
            if not position:
                return {"error": "Position not found"}

            # Calculate delta
            delta_info = self.calculate_position_delta(position)
            if "error" in delta_info:
                return {"error": delta_info["error"]}

            # Robust delta extraction and validation
            delta = delta_info.get("delta")
            if delta is None:
                return {"error": "Delta calculation failed: No delta value returned"}

            # Ensure delta is a numeric value
            try:
                delta = float(delta)
            except (TypeError, ValueError):
                return {"error": f"Invalid delta value: {delta}"}

            # Detailed logging
            logger.info(f"Position details for hedging:")
            logger.info(f"Position ID: {position_id}")
            logger.info(f"Delta: {delta}")
            logger.info(f"Position size: {position.size}")
            logger.info(f"Contract size: {position.contract_size}")
            logger.info(f"Time to expiry: {position.time_to_expiry}")

            # Near-expiry or zero delta handling
            if delta == 0 or position.time_to_expiry <= 0.001:
                # For near-expiry positions, use a small hedge
                hedge_size = max(0.01, position.size * 0.1)  # 10% of position size
                logger.info(f"Near-expiry position - Using minimal hedge: {hedge_size}")
            else:
                # Normal delta-based hedge size calculation
                hedge_size = abs(delta) * position.size * position.contract_size

            # Apply min and max constraints
            hedge_size = max(0.01, hedge_size)  # Minimum 0.01
            hedge_size = min(hedge_size, 100.0)  # Maximum 100

            logger.info(f"Final hedge size: {hedge_size}")

            # Determine hedge direction
            direction = OrderDirection.BUY if hedge_size > 0 else OrderDirection.SELL

            # Create hedge position
            hedge_result = self.ig_client.create_hedge_position(
                epic=position.underlying_epic, direction=direction, size=abs(hedge_size)
            )

            return {
                "status": "hedged",
                "hedge_size": hedge_size,
                "hedge_reference": hedge_result.get("dealReference"),
                "delta": delta_info,
            }

        except Exception as e:
            logger.error(f"Comprehensive hedging error: {str(e)}")
            return {"error": f"Hedging failed: {str(e)}"}

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
        """Get status for all positions"""
        try:
            positions_status = {}
            positions_data = self.ig_client.get_positions()

            if "error" in positions_data:
                return {"error": positions_data["error"]}

            for pos_data in positions_data.get("positions", []):
                try:
                    position = Position.from_dict(pos_data)
                    if not position:
                        continue

                    delta_info = self.calculate_position_delta(position)
                    metrics = self.calculate_position_metrics(position)

                    positions_status[position.deal_id] = {
                        "position": position.to_dict(),
                        "delta": delta_info,
                        "metrics": metrics,
                        "needs_hedge": delta_info.get("needs_hedge", False),
                    }
                except Exception as e:
                    logger.error(f"Error processing position status: {str(e)}")
                    continue

            return positions_status

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
            if not isinstance(settings, dict):
                return {"error": "Settings must be a dictionary"}

            # Convert numeric values
            try:
                settings = {
                    "min_hedge_size": float(settings.get("min_hedge_size", 0)),
                    "max_hedge_size": float(settings.get("max_hedge_size", 0)),
                    "hedge_interval": float(settings.get("hedge_interval", 0)),
                    "delta_threshold": float(settings.get("delta_threshold", 0)),
                }
            except (ValueError, TypeError):
                return {"error": "Invalid numeric values in settings"}

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

    def start_monitoring(self, interval: float, delta_threshold: float) -> Dict:
        """Start automated monitoring of positions"""
        try:
            self.hedge_interval = interval
            self.delta_threshold = delta_threshold
            self.monitoring_active = True
            self.last_check_time = datetime.now()

            return {
                "status": "success",
                "message": "Monitoring started",
                "settings": {"interval": interval, "delta_threshold": delta_threshold},
            }
        except Exception as e:
            logger.error(f"Failed to start monitoring: {str(e)}")
            return {"error": str(e)}

    def get_sold_positions(self) -> Dict:
        """Get all sold positions"""
        try:
            positions_data = self.ig_client.get_positions()
            if "error" in positions_data:
                return {"error": positions_data["error"]}

            sold_positions = []
            for pos_data in positions_data.get("positions", []):
                try:
                    position = Position.from_dict(pos_data)
                    if position and position.direction.upper() == "SELL":
                        metrics = self.calculate_position_metrics(position)
                        delta_info = self.calculate_position_delta(position)

                        position_dict = position.to_dict()
                        position_dict.update({"metrics": metrics, "delta": delta_info})
                        sold_positions.append(position_dict)
                except Exception as e:
                    logger.error(f"Error processing sold position: {str(e)}")
                    continue

            return {"positions": sold_positions, "count": len(sold_positions)}

        except Exception as e:
            logger.error(f"Error getting sold positions: {str(e)}")
            return {"error": str(e)}
