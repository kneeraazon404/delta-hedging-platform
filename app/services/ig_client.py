# app/services/ig_client.py
import logging
import os
import time
from datetime import datetime
from typing import Dict, Optional

import requests
from dotenv import load_dotenv

from app.models.enums import OrderDirection, OrderType
from app.services.mock_market import MockMarketData
from config.settings import HEDGE_SETTINGS as _hedge_settings

load_dotenv()


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

    def get_positions(self) -> Dict:
        """
        Get positions from IG API with proper error handling.
        """
        # if self.use_mock:
        #     return {"positions": []}  # Return empty positions list when using mock data

        try:
            # Step 1: Get positions
            position_headers = self.get_headers(version="2")

            position_response = self.session.get(
                f"{self.base_url}/positions", headers=position_headers, timeout=30
            )
            position_response.raise_for_status()

            positions_data = position_response.json()

            # Step 2: Get detailed position information for each position
            for position in positions_data.get("positions", []):
                deal_id = position["position"]["dealId"]
                position_details = self.session.get(
                    f"{self.base_url}/positions/{deal_id}",
                    headers=position_headers,
                    timeout=30,
                )
                if position_details.status_code == 200:
                    position["details"] = position_details.json()

            return positions_data

        except requests.exceptions.RequestException as e:
            logging.error(f"API request failed: {str(e)}")
            return {"error": f"API request failed: {str(e)}"}

    def _calculate_time_to_expiry(self, expiry_str: str) -> float:
        """Calculate time to expiry in years"""
        try:
            expiry_date = datetime.strptime(expiry_str, "%d-%b-%y")
            days_to_expiry = (expiry_date - datetime.now()).days
            return max(
                days_to_expiry / 365, 0.0001
            )  # Minimum 0.0001 to avoid division by zero
        except Exception as e:
            logging.error(f"Error calculating time to expiry: {str(e)}")
            return 0.25  # Default to 3 months if calculation fails

    def parse_position_data(self, position_data: Dict) -> Dict:
        """Parse and extract relevant information from position data."""
        positions = []
        for pos in position_data.get("positions", []):
            try:
                position_info = pos["position"]
                market_info = pos["market"]

                # Extract option details
                instrument_name = market_info["instrumentName"]
                name_parts = instrument_name.split()
                strike = float(name_parts[-2])  # Extract strike from name
                option_type = "PUT" if "PUT" in instrument_name else "CALL"

                # Get underlying epic
                underlying_epic = self.get_underlying_epic(instrument_name)

                # Calculate premium
                size = float(position_info["size"])
                level = float(position_info["level"])
                premium = size * level

                parsed_position = {
                    "deal_id": position_info["dealId"],
                    "size": size,
                    "direction": position_info["direction"],
                    "level": level,
                    "premium": premium,
                    "currency": position_info["currency"],
                    "instrument_name": instrument_name,
                    "underlying_epic": underlying_epic,
                    "strike": strike,
                    "option_type": option_type,
                    "expiry": market_info["expiry"],
                    "current_bid": float(market_info["bid"]),
                    "current_offer": float(market_info["offer"]),
                    "market_status": market_info["marketStatus"],
                    "time_to_expiry": self._calculate_time_to_expiry(
                        market_info["expiry"]
                    ),
                }
                positions.append(parsed_position)

            except KeyError as e:
                logging.error(f"Error parsing position data: {str(e)}")
                continue

        return {"positions": positions}

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

    def get_underlying_epic(self, option_name: str) -> str:
        """Get underlying market epic from option name"""
        if "Wall Street" in option_name:
            return "IX.D.DOW.IFD.IP"  # DOW epic
        elif "US Tech 100" in option_name:
            return "IX.D.NASDAQ.IFD.IP"  # NASDAQ epic
        else:
            raise ValueError(f"Unknown underlying for option: {option_name}")

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

    def get_underlying_data(self, underlying_epic: str) -> Dict:
        """Get market data for underlying asset"""
        try:
            if self.use_mock:
                return self.mock_data.get_market_data()

            self._rate_limit()
            response = self.session.get(
                f"{self.base_url}/markets/{underlying_epic}",
                headers=self.get_headers(version="3"),
            )

            if response.status_code == 200:
                data = response.json()
                snapshot = data.get("snapshot", {})
                return {
                    "bid": float(snapshot.get("bid", 0)),
                    "offer": float(snapshot.get("offer", 0)),
                    "high": float(snapshot.get("high", 0)),
                    "low": float(snapshot.get("low", 0)),
                    "update_time": snapshot.get("updateTime"),
                    "volatility": max(
                        0.001, abs(float(snapshot.get("percentageChange", 0.1)) / 100)
                    ),
                }
            else:
                logging.warning(f"Failed to get underlying data, using mock data")
                return self.mock_data.get_market_data()

        except Exception as e:
            logging.error(f"Error getting underlying data: {str(e)}")
            return self.mock_data.get_market_data()

    def create_hedge_position(
        self,
        underlying_epic: str,
        size: float,
        direction: OrderDirection,
    ) -> Dict:
        """Create a hedge position in the underlying"""
        try:
            return self.create_position(
                direction=direction,
                epic=underlying_epic,
                size=size,
                order_type=OrderType.MARKET,
            )
        except Exception as e:
            logging.error(f"Error creating hedge position: {str(e)}")
            raise
