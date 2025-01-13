# app.py
import logging
import os
import time
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

import numpy as np
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS  # Add this import
from scipy.stats import norm

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    filename="delta_hedger.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Global storage
_positions = {}
_hedge_settings = {
    "min_hedge_size": 0.01,
    "max_hedge_size": 100.0,
    "hedge_interval": 60,  # seconds
    "default_rate": 0.05,  # Risk-free rate
    "min_volatility": 0.001,
    "max_volatility": 2.0,
}


class OrderDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class OptionType(Enum):
    CALL = "CALL"
    PUT = "PUT"


class IGClient:
    def __init__(self, use_mock: bool = True):
        self.session = requests.Session()
        self.base_url = "https://demo-api.ig.com/gateway/deal"
        self.api_key = os.getenv("IG_API_KEY")
        self.username = os.getenv("IG_USERNAME")
        self.password = os.getenv("IG_PASSWORD")
        self.acc_type = os.getenv("IG_ACC_TYPE", "DEMO")
        self.security_token = None
        self.cst = None
        self.last_request_time = 0
        self.use_mock = use_mock
        self.mock_data = MockMarketData()

        if not use_mock:
            self.login()

    def _validate_credentials(self):
        """Validate API credentials"""
        if not all([self.api_key, self.username, self.password]):
            raise ValueError("Missing IG API credentials in environment variables")

    def _handle_response(self, response: requests.Response, operation: str) -> Dict:
        """Handle API response and errors"""
        try:
            if response.status_code in [200, 201]:
                return response.json()
            else:
                error_msg = f"{operation} failed: {response.text}"
                logging.error(error_msg)
                raise Exception(error_msg)
        except Exception as e:
            logging.error(f"Error handling response: {str(e)}")
            raise

    def login(self):
        """Enhanced login to IG API with retry logic"""
        self._validate_credentials()
        max_retries = 3
        retry_delay = 5  # seconds

        for attempt in range(max_retries):
            try:
                headers = {
                    "X-IG-API-KEY": self.api_key,
                    "Version": "2",
                    "Content-Type": "application/json",
                }

                data = {
                    "identifier": self.username,
                    "password": self.password,
                    "encryptedPassword": False,
                }

                response = self.session.post(
                    f"{self.base_url}/session", headers=headers, json=data
                )

                if response.status_code == 200:
                    self.security_token = response.headers.get("X-SECURITY-TOKEN")
                    self.cst = response.headers.get("CST")
                    logging.info("Successfully logged in to IG API")

                    # Get account details
                    self.get_account_info()
                    return
                else:
                    logging.error(
                        f"Login attempt {attempt + 1} failed: {response.text}"
                    )
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    raise Exception(f"Login failed after {max_retries} attempts")

            except Exception as e:
                logging.error(f"Login error on attempt {attempt + 1}: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                raise

    def _rate_limit(self):
        """Implement rate limiting"""
        current_time = time.time()
        if current_time - self.last_request_time < _hedge_settings["hedge_interval"]:
            wait_time = _hedge_settings["hedge_interval"] - (
                current_time - self.last_request_time
            )
            time.sleep(wait_time)
        self.last_request_time = time.time()

    def get_headers(self, version="2"):
        """Get headers for API requests with version control"""
        if not all([self.security_token, self.cst]):
            self.login()  # Re-login if tokens are missing

        return {
            "X-IG-API-KEY": self.api_key,
            "X-SECURITY-TOKEN": self.security_token,
            "CST": self.cst,
            "Content-Type": "application/json",
            "Version": version,
            "Accept": "application/json; charset=UTF-8",
        }

    def get_account_info(self) -> Optional[Dict]:
        """Get account details"""
        try:
            response = self.session.get(
                f"{self.base_url}/accounts", headers=self.get_headers(version="1")
            )
            return self._handle_response(response, "Get account info")
        except Exception as e:
            logging.error(f"Account info error: {str(e)}")
            return None

    def get_market_data(self, epic: str) -> Dict:
        """Get market data with mock fallback"""
        if self.use_mock:
            return self.mock_data.get_market_data()

        try:
            self._rate_limit()
            response = self.session.get(
                f"{self.base_url}/markets/{epic}", headers=self.get_headers(version="3")
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "price": float(data["snapshot"]["bid"]),
                    "volatility": max(
                        0.001, abs(float(data["snapshot"]["percentageChange"]) / 100)
                    ),
                    "high": float(data["snapshot"]["high"]),
                    "low": float(data["snapshot"]["low"]),
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                logging.warning(f"Failed to get market data from API, using mock data")
                return self.mock_data.get_market_data()

        except Exception as e:
            logging.error(f"Market data error: {str(e)}, using mock data")
            return self.mock_data.get_market_data()

    def create_position(
        self,
        direction: OrderDirection,
        epic: str,
        size: float,
        order_type: OrderType = OrderType.MARKET,
    ) -> Dict:
        """Create a new position"""
        try:
            data = {
                "epic": epic,
                "expiry": "-",
                "direction": direction.value,
                "size": str(size),
                "orderType": order_type.value,
                "timeInForce": "FILL_OR_KILL",
                "guaranteedStop": False,
                "forceOpen": True,
            }

            response = self.session.post(
                f"{self.base_url}/positions/otc",
                headers=self.get_headers(version="2"),
                json=data,
            )
            return self._handle_response(response, "Create position")

        except Exception as e:
            logging.error(f"Position creation error: {str(e)}")
            raise

    def close_position(self, deal_id: str, direction: OrderDirection) -> Dict:
        """Close a position"""
        try:
            data = {
                "dealId": deal_id,
                "direction": direction.value,
                "size": "ALL",
                "orderType": OrderType.MARKET.value,
            }

            response = self.session.post(
                f"{self.base_url}/positions/otc",
                headers=self.get_headers(version="1"),
                json=data,
            )
            return self._handle_response(response, "Close position")

        except Exception as e:
            logging.error(f"Position closure error: {str(e)}")
            raise


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


class OptionCalculator:
    def __init__(self, rate: float = _hedge_settings["default_rate"]):
        self.rate = rate

    def validate_inputs(self, S: float, K: float, T: float, sigma: float):
        """Validate inputs for delta calculation"""
        if S <= 0:
            raise ValueError("Stock price (S) must be positive")
        if K <= 0:
            raise ValueError("Strike price (K) must be positive")
        if T <= 0:
            raise ValueError("Time to expiry (T) must be positive")
        if sigma <= 0:
            raise ValueError("Volatility (sigma) must be positive")

    def calculate_delta(
        self, S: float, K: float, T: float, sigma: float, option_type: OptionType
    ) -> float:
        """Calculate option delta using Black-Scholes formula with input validation"""
        try:
            # Validate inputs
            self.validate_inputs(S, K, T, sigma)

            # Add small epsilon to avoid division by zero
            epsilon = 1e-10
            T = max(T, epsilon)
            sigma = max(sigma, epsilon)

            d1 = (np.log(S / K) + (self.rate + sigma**2 / 2) * T) / (sigma * np.sqrt(T))

            if option_type == OptionType.CALL:
                delta = float(norm.cdf(d1))
            else:
                delta = float(norm.cdf(d1) - 1)

            logging.info(
                f"Delta calculated: {delta} for S={S}, K={K}, T={T}, sigma={sigma}"
            )
            return delta
        except Exception as e:
            logging.error(f"Delta calculation error: {str(e)}")
            raise


class MockMarketData:
    def __init__(self):
        self.base_price = 1.2000
        self.volatility = 0.02
        self.last_update = time.time()
        self.update_interval = 1  # 1 second

    def get_price(self) -> float:
        """Generate a realistic mock price"""
        current_time = time.time()
        time_diff = current_time - self.last_update

        if time_diff >= self.update_interval:
            # Add random walk
            random_walk = np.random.normal(0, self.volatility * np.sqrt(time_diff))
            self.base_price *= 1 + random_walk
            self.last_update = current_time

        return self.base_price

    def get_market_data(self) -> Dict:
        """Get complete market data"""
        current_price = self.get_price()
        return {
            "price": current_price,
            "volatility": self.volatility,
            "high": current_price * 1.01,
            "low": current_price * 0.99,
            "timestamp": datetime.now().isoformat(),
            "instrument_type": "CURRENCIES",
            "expiry": "-",
            "lot_size": 1,
            "currency": "USD",
            "bid": current_price - 0.0001,
            "ask": current_price + 0.0001,
            "spread": 0.0002,
        }


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


# Initialize Flask application
app = Flask(__name__)
CORS(app)


# Create API endpoints
@app.route("/api/positions", methods=["POST"])
def create_position():
    """Create a new position"""
    try:
        data = request.get_json()
        position_id = hedger.create_position(data)
        return jsonify(
            {
                "position_id": position_id,
                "position": hedger.positions[position_id].to_dict(),
            }
        )
    except Exception as e:
        logging.error(f"Position creation error: {str(e)}")
        return jsonify({"error": str(e)}), 400


@app.route("/api/positions/<position_id>", methods=["GET"])
def get_position(position_id):
    """Get position details with improved error handling"""
    try:
        position = hedger.get_position(position_id)
        if not position:
            return (
                jsonify(
                    {
                        "error": "Position not found",
                        "available_positions": list(hedger.positions.keys()),
                    }
                ),
                404,
            )

        # Get current market data
        market_data = hedger.ig_client.mock_data.get_market_data()
        current_price = market_data["price"]
        pnl = hedger.calculate_pnl(position, current_price)

        return jsonify(
            {
                "position": position.to_dict(),
                "active": position.is_active,
                "total_hedges": len(position.hedge_history),
                "current_status": {
                    "current_price": current_price,
                    "current_pnl": pnl,
                    "needs_hedge": pnl <= -position.premium,
                },
            }
        )

    except Exception as e:
        logging.error(f"Error getting position {position_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/positions", methods=["GET"])
def list_positions():
    """List all positions with proper error handling"""
    try:
        positions = hedger.list_positions()
        # Get current market data for all positions
        positions_with_status = {}
        for pid, pos_data in positions.items():
            position = hedger.get_position(pid)
            if position:
                market_data = hedger.ig_client.mock_data.get_market_data()
                current_price = market_data["price"]
                pnl = hedger.calculate_pnl(position, current_price)

                positions_with_status[pid] = {
                    **pos_data,
                    "current_status": {
                        "current_price": current_price,
                        "current_pnl": pnl,
                        "needs_hedge": pnl <= -position.premium,
                    },
                }

        return jsonify({"positions": positions_with_status, "count": len(positions)})
    except Exception as e:
        logging.error(f"Error listing positions: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/market-data/<position_id>", methods=["GET"])
def get_market_data(position_id):
    """Get market data and analysis for a position"""
    try:
        position = hedger.get_position(position_id)
        if not position:
            return (
                jsonify(
                    {
                        "error": "Position not found",
                        "available_positions": list(hedger.positions.keys()),
                    }
                ),
                404,
            )

        # Get market data using mock data
        market_data = hedger.ig_client.mock_data.get_market_data()
        current_price = market_data["price"]

        # Calculate PnL
        pnl = hedger.calculate_pnl(position, current_price)

        # Calculate time remaining
        time_now = datetime.now()
        created_at = datetime.fromisoformat(position.created_at)
        remaining_time = position.time_to_expiry - (
            time_now - created_at
        ).total_seconds() / (365 * 24 * 60 * 60)

        response = {
            "position_id": position_id,
            "market_data": market_data,
            "analysis": {
                "current_price": current_price,
                "current_pnl": pnl,
                "time_remaining": remaining_time,
                "hedge_size": position.hedge_size,
                "needs_hedge": pnl <= -position.premium,
                "premium_threshold": -position.premium,
            },
            "position_status": {
                "is_active": position.is_active,
                "total_hedges": len(position.hedge_history),
                "last_hedge_time": position.last_hedge_time,
                "pnl_threshold_crossed": position.pnl_threshold_crossed,
            },
        }

        return jsonify(response)

    except Exception as e:
        logging.error(f"Error getting market data for position {position_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/hedge/<position_id>", methods=["POST"])
def hedge_position(position_id):
    """Trigger hedging for a specific position"""
    try:
        result = hedger.hedge_position(position_id)
        if "error" in result:
            return jsonify(result), 400

        # Add market data to result
        market_data = hedger.ig_client.mock_data.get_market_data()
        result["market_data"] = market_data

        return jsonify(result)
    except Exception as e:
        logging.error(f"Hedging error: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/hedge/all", methods=["POST"])
def hedge_all_positions():
    """Hedge all active positions"""
    try:
        results = {}
        market_data = hedger.ig_client.mock_data.get_market_data()

        for position_id, position in hedger.positions.items():
            if position.is_active:
                result = hedger.hedge_position(position_id)
                result["market_data"] = market_data
                results[position_id] = result

        return jsonify(results)
    except Exception as e:
        logging.error(f"Error hedging all positions: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/hedge/status", methods=["GET"])
def get_hedge_status():
    """Get hedging status for all active positions"""
    try:
        status = {}
        for position_id, position in hedger.positions.items():
            if position.is_active:
                market_data = hedger.ig_client.mock_data.get_market_data()
                current_price = market_data["price"]
                pnl = hedger.calculate_pnl(position, current_price)

                # Calculate time remaining
                time_now = datetime.now()
                created_at = datetime.fromisoformat(position.created_at)
                remaining_time = position.time_to_expiry - (
                    time_now - created_at
                ).total_seconds() / (365 * 24 * 60 * 60)

                status[position_id] = {
                    "market_data": market_data,
                    "position_status": {
                        "current_price": current_price,
                        "current_pnl": pnl,
                        "hedge_size": position.hedge_size,
                        "last_hedge_time": position.last_hedge_time,
                        "needs_hedge": pnl <= -position.premium,
                        "time_remaining": remaining_time,
                        "is_active": position.is_active,
                        "total_hedges": len(position.hedge_history),
                    },
                }
        return jsonify(status)
    except Exception as e:
        logging.error(f"Get hedge status error: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/positions/<position_id>/history", methods=["GET"])
def get_position_history(position_id):
    """Get position history"""
    try:
        position = hedger.positions.get(position_id)
        if not position:
            return jsonify({"error": "Position not found"}), 404

        # Get current market data
        market_data = hedger.ig_client.mock_data.get_market_data()
        current_price = market_data["price"]
        pnl = hedger.calculate_pnl(position, current_price)

        return jsonify(
            {
                "position": position.to_dict(),
                "hedge_history": [
                    record.to_dict() for record in position.hedge_history
                ],
                "current_status": {
                    "current_price": current_price,
                    "current_pnl": pnl,
                    "needs_hedge": pnl <= -position.premium,
                    "market_data": market_data,
                },
            }
        )
    except Exception as e:
        logging.error(f"Error getting position history: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings", methods=["GET", "POST"])
def handle_settings():
    """Get or update hedge settings"""
    try:
        if request.method == "POST":
            data = request.get_json()
            for key, value in data.items():
                if key in _hedge_settings:
                    _hedge_settings[key] = type(_hedge_settings[key])(value)

            # Update hedger settings
            hedger.min_hedge_size = _hedge_settings["min_hedge_size"]
            hedger.max_hedge_size = _hedge_settings["max_hedge_size"]
            hedger.hedge_interval = _hedge_settings["hedge_interval"]

        return jsonify(
            {
                "settings": _hedge_settings,
                "active_positions": len(
                    [p for p in hedger.positions.values() if p.is_active]
                ),
            }
        )
    except Exception as e:
        logging.error(f"Settings error: {str(e)}")
        return jsonify({"error": str(e)}), 500


# Initialize with proper logging
ig_client = IGClient(use_mock=True)  # Use mock data by default
hedger = DeltaHedger(ig_client)
logging.info("Application initialized with mock market data")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
