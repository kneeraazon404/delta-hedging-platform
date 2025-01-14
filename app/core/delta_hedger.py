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
        # Store positions in instance variable instead of global
        self.positions: Dict[str, Position] = {}
        self.min_hedge_size = _hedge_settings["min_hedge_size"]
        self.max_hedge_size = _hedge_settings["max_hedge_size"]
        self.hedge_interval = _hedge_settings["hedge_interval"]

    def create_position(self, data: Dict) -> str:
        """Create a new position with proper validation"""
        try:
            # Validate required fields
            required_fields = [
                "epic",
                "strike",
                "option_type",
                "premium",
                "contracts",
                "time_to_expiry",
            ]
            if not all(field in data for field in required_fields):
                raise ValueError(
                    f"Missing required fields. Required: {required_fields}"
                )

            # Create position
            position_id = str(int(time.time()))
            position = Position(data)

            # Store in instance dictionary
            self.positions[position_id] = position

            logging.info(f"Created position {position_id}: {data}")
            logging.info(f"Total positions: {len(self.positions)}")

            return position_id
        except Exception as e:
            logging.error(f"Position creation error: {str(e)}")
            raise

    def get_position(self, position_id: str) -> Optional[Position]:
        """Get position by ID with proper logging"""
        position = self.positions.get(position_id)
        if position:
            logging.info(f"Retrieved position {position_id}")
        else:
            logging.error(
                f"Position {position_id} not found. Available positions: {list(self.positions.keys())}"
            )
        return position

    def list_positions(self) -> Dict[str, Dict]:
        """List all positions with their details"""
        return {pid: pos.to_dict() for pid, pos in self.positions.items()}

    def _validate_position_data(self, data: Dict):
        """Validate position creation data"""
        required_fields = [
            "epic",
            "strike",
            "option_type",
            "premium",
            "contracts",
            "time_to_expiry",
        ]
        if not all(field in data for field in required_fields):
            raise ValueError(f"Missing required fields. Required: {required_fields}")

        try:
            OptionType(data["option_type"].upper())
        except ValueError:
            raise ValueError("Invalid option_type. Must be 'CALL' or 'PUT'")

    def calculate_pnl(self, position: Position, current_price: float) -> float:
        """Calculate position PnL"""
        try:
            option_value = (
                max(0, current_price - position.strike)
                if position.option_type == OptionType.CALL
                else max(0, position.strike - current_price)
            )
            return position.premium - (option_value * position.contracts)
        except Exception as e:
            logging.error(f"PnL calculation error: {str(e)}")
            raise

    def hedge_position(self, position_id: str) -> Dict:
        """Hedge a specific position"""
        position = self.positions.get(position_id)
        if not position or not position.is_active:
            return {"error": "Position not found or inactive"}

        try:
            market_data = self.ig_client.get_market_data(position.epic)
            position.last_market_data = market_data  # type: ignore
            current_price = market_data["price"]

            # Update time to expiry
            time_now = datetime.now()
            created_at = datetime.fromisoformat(position.created_at)
            remaining_time = position.time_to_expiry - (
                time_now - created_at
            ).total_seconds() / (365 * 24 * 60 * 60)

            if remaining_time <= 0:
                position.is_active = False
                return {"error": "Position expired", "position_id": position_id}

            # Calculate PnL
            pnl = self.calculate_pnl(position, current_price)

            # Check if hedging is needed
            if pnl <= -position.premium:
                position.pnl_threshold_crossed = True

                # Calculate delta
                delta = self.calculator.calculate_delta(
                    current_price,
                    position.strike,
                    remaining_time,
                    market_data["volatility"],
                    position.option_type,
                )

                # Calculate hedge size
                target_hedge = max(
                    self.min_hedge_size,
                    min(self.max_hedge_size, abs(-delta * position.contracts)),
                )

                hedge_adjustment = target_hedge - position.hedge_size

                # Execute hedge if adjustment is significant
                if abs(hedge_adjustment) >= self.min_hedge_size:
                    direction = (
                        OrderDirection.BUY
                        if hedge_adjustment > 0
                        else OrderDirection.SELL
                    )
                    result = self.ig_client.create_position(
                        direction=direction,
                        epic=position.epic,
                        size=abs(hedge_adjustment),
                    )
                    position.deal_id = result.get("dealId")

                    # Record hedge
                    position.add_hedge_record(delta, target_hedge, current_price, pnl)

                    return {
                        "position_id": position_id,
                        "hedge_adjustment": hedge_adjustment,
                        "new_hedge_size": target_hedge,
                        "current_pnl": pnl,
                        "time_remaining": remaining_time,
                        "delta": delta,
                        "deal_id": position.deal_id,
                    }

            elif pnl > -position.premium and position.pnl_threshold_crossed:
                position.pnl_threshold_crossed = False
                logging.info(f"Position {position_id} - PnL improved above threshold")

                # Close hedge position if exists
                if position.deal_id:
                    self.ig_client.close_position(
                        position.deal_id,
                        (
                            OrderDirection.SELL
                            if position.hedge_size > 0
                            else OrderDirection.BUY
                        ),
                    )
                    position.deal_id = None
                    position.hedge_size = 0

            return {
                "message": "No hedge needed",
                "current_pnl": pnl,
                "time_remaining": remaining_time,
                "position_id": position_id,
            }

        except Exception as e:
            logging.error(f"Hedging error for position {position_id}: {str(e)}")
            return {"error": str(e)}
