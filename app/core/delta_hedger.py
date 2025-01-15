# app/core/delta_hedger.py
import logging
import time
from datetime import datetime
from typing import Dict, Optional

from app.core.option_calculator import OptionCalculator
from app.models.enums import OptionType, OrderDirection
from app.models.position import Position
from app.services.ig_client import IGClient
from config.settings import HEDGE_SETTINGS as _hedge_settings


class DeltaHedger:
    def __init__(self, ig_client: IGClient):
        self.ig_client = ig_client
        self.calculator = OptionCalculator()
        self.positions: Dict[str, Position] = {}
        self.min_hedge_size = _hedge_settings["min_hedge_size"]
        self.max_hedge_size = _hedge_settings["max_hedge_size"]
        self.hedge_interval = _hedge_settings["hedge_interval"]

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get position by ID with proper validation"""
        try:
            position = self.positions.get(position_id)
            if position:
                logging.info(f"Retrieved position {position_id}")
                return position

            # If not in local cache, check IG positions
            positions_data = self.ig_client.get_positions()
            parsed_data = self.ig_client.parse_position_data(positions_data)

            for pos in parsed_data.get("positions", []):
                if pos["deal_id"] == position_id:
                    # Create new Position object
                    position = Position(pos)
                    self.positions[position_id] = position
                    return position

            logging.error(f"Position {position_id} not found")
            return None

        except Exception as e:
            logging.error(f"Error getting position {position_id}: {str(e)}")
        return None

    def monitor_positions(self) -> Dict:
        """Monitor all positions for hedging needs"""
        try:
            # Get all positions from IG
            positions_data = self.ig_client.get_positions()
            parsed_data = self.ig_client.parse_position_data(positions_data)

            results = {}
            for position in parsed_data["positions"]:
                if position["direction"] == "SELL":  # Only hedge sold positions
                    position_id = position["deal_id"]

                    # Create or update position object
                    if position_id not in self.positions:
                        self.positions[position_id] = Position(position)

                    # Check if hedging is needed
                    result = self.hedge_position(position_id)
                    results[position_id] = result

            return {"results": results}

        except Exception as e:
            logging.error(f"Error monitoring positions: {str(e)}")
            return {"error": str(e)}

    def hedge_position(self, position_id: str) -> Dict:
        """Hedge a specific position"""
        position = self.positions.get(position_id)
        if not position or not position.is_active:
            return {"error": "Position not found or inactive"}

        try:
            # Get underlying market data
            underlying_data = self.ig_client.get_underlying_data(
                position.underlying_epic  # type: ignore
            )
            current_price = (underlying_data["bid"] + underlying_data["offer"]) / 2
            volatility = underlying_data["volatility"]

            # Check if position is expired
            if position.time_to_expiry <= 0:
                position.is_active = False
                return {"error": "Position expired", "position_id": position_id}

            # Calculate PnL
            pnl = self.calculate_pnl(position, current_price)

            # Check if hedging is needed based on PnL threshold
            if position.needs_hedge(pnl):
                # Calculate Greeks
                greeks = self.calculator.calculate_greeks(
                    current_price,
                    position.strike,
                    position.time_to_expiry,
                    volatility,
                    position.option_type,
                )

                # Calculate required hedge size
                target_hedge = self.calculator.calculate_hedge_size(
                    greeks["delta"],
                    position.contracts,
                    self.min_hedge_size,
                    self.max_hedge_size,
                )

                # Get hedge direction
                hedge_direction = position.get_hedge_direction(target_hedge)

                if hedge_direction:
                    # Execute hedge
                    result = self.ig_client.create_hedge_position(
                        underlying_epic=position.underlying_epic,  # type: ignore
                        size=abs(target_hedge - position.hedge_size),
                        direction=hedge_direction,
                    )

                    # Update position state
                    position.update_hedge_state(result.get("dealId"))
                    position.add_hedge_record(
                        greeks["delta"], target_hedge, current_price, pnl
                    )

                    return {
                        "position_id": position_id,
                        "action": "hedged",
                        "hedge_size": target_hedge,
                        "current_pnl": pnl,
                        "greeks": greeks,
                        "deal_id": result.get("dealId"),
                    }

            # Remove hedge if PnL improves
            elif pnl > -position.premium and position.pnl_threshold_crossed:
                if position.hedge_deal_id:
                    # Close hedge position
                    self.ig_client.close_position(
                        position.hedge_deal_id,
                        (
                            OrderDirection.SELL
                            if position.hedge_size > 0
                            else OrderDirection.BUY
                        ),
                    )
                    position.update_hedge_state(None)

                return {
                    "position_id": position_id,
                    "action": "hedge_removed",
                    "current_pnl": pnl,
                }

            return {
                "position_id": position_id,
                "action": "no_action",
                "current_pnl": pnl,
                "needs_hedge": position.needs_hedge(pnl),
            }

        except Exception as e:
            logging.error(f"Hedging error for position {position_id}: {str(e)}")
            return {"error": str(e)}

    def calculate_pnl(self, position: Position, current_price: float) -> float:
        """Calculate position PnL taking into account hedge positions"""
        try:
            # Calculate option value
            option_value = (
                max(0, current_price - position.strike)
                if position.option_type == OptionType.CALL
                else max(0, position.strike - current_price)
            )

            # Calculate PnL from option
            option_pnl = position.premium - (option_value * position.contracts)

            # Add PnL from hedge if exists
            if position.hedge_size > 0:
                last_hedge = position.get_last_hedge()
                if last_hedge:
                    hedge_pnl = position.hedge_size * (current_price - last_hedge.price)
                    option_pnl += hedge_pnl

            return option_pnl

        except Exception as e:
            logging.error(f"PnL calculation error: {str(e)}")
            raise
