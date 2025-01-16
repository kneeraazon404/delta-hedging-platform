# app/services/ig_client.py
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Union

import requests
from dotenv import load_dotenv

from app.models.enums import OrderDirection, OrderType
from app.services.mock_market import MockMarketData
from config.settings import HEDGE_SETTINGS as _hedge_settings

load_dotenv()
logger = logging.getLogger(__name__)


class IGClient:
    def __init__(self, use_mock: bool = False):
        """Initialize IGClient with configuration"""
        self.session = requests.Session()
        self.base_url = "https://demo-api.ig.com/gateway/deal"

        # API credentials
        self.api_key = os.getenv("IG_API_KEY")
        self.username = os.getenv("IG_USERNAME")
        self.password = os.getenv("IG_PASSWORD")
        self.acc_type = os.getenv("IG_ACC_TYPE", "DEMO")

        # Authentication tokens
        self.security_token: Optional[str] = None
        self.cst: Optional[str] = None
        self.token_expiry: Optional[datetime] = None

        # Rate limiting
        self.last_request_time = 0
        self.request_interval = float(_hedge_settings.get("api_request_interval", 1.0))

        # Mock data handling
        self.use_mock = use_mock
        self.mock_data = MockMarketData() if use_mock else None

        # Authenticate if not using mock
        if not use_mock:
            self.login()

    def _validate_credentials(self) -> None:
        """Validate API credentials"""
        missing = []
        if not self.api_key:
            missing.append("IG_API_KEY")
        if not self.username:
            missing.append("IG_USERNAME")
        if not self.password:
            missing.append("IG_PASSWORD")

        if missing:
            raise ValueError(f"Missing IG API credentials: {', '.join(missing)}")

    def _handle_response(self, response: requests.Response, operation: str) -> Dict:
        """Handle API response and errors"""
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_msg = f"{operation} failed with status {response.status_code}: {response.text}"
            logger.error(error_msg)
            return {"error": error_msg, "status_code": response.status_code}
        except requests.exceptions.JSONDecodeError as e:
            error_msg = f"Invalid JSON response for {operation}: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}
        except Exception as e:
            error_msg = f"Error handling response for {operation}: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}

    def _check_token_expiry(self) -> bool:
        """Check if authentication token needs refresh"""
        if not self.token_expiry:
            return True
        return datetime.now() >= self.token_expiry

    def login(self) -> bool:
        """Authenticate with IG API"""
        try:
            self._validate_credentials()

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
                f"{self.base_url}/session", headers=headers, json=data, timeout=30
            )

            if response.status_code == 200:
                self.security_token = response.headers.get("X-SECURITY-TOKEN")
                self.cst = response.headers.get("CST")
                # Set token expiry to 6 hours from now
                self.token_expiry = datetime.now() + timedelta(hours=6)
                logger.info("Successfully logged in to IG API")
                return True

            logger.error(f"Login failed: {response.text}")
            return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Login request error: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            return False

    def get_positions(self) -> Dict:
        """Get all positions from IG API"""
        if self.use_mock:
            return {"positions": []}

        try:
            if self._check_token_expiry():
                if not self.login():
                    return {"error": "Failed to refresh authentication"}

            # Get positions list
            response = self.session.get(
                f"{self.base_url}/positions", headers=self.get_headers(), timeout=30
            )

            positions_data = self._handle_response(response, "Get positions")
            if "error" in positions_data:
                return positions_data

            # Get detailed information for each position
            for position in positions_data.get("positions", []):
                deal_id = position.get("position", {}).get("dealId")
                if not deal_id:
                    continue

                details_response = self.session.get(
                    f"{self.base_url}/positions/{deal_id}",
                    headers=self.get_headers(),
                    timeout=30,
                )

                if details_response.status_code == 200:
                    position["details"] = details_response.json()

            return positions_data

        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to get positions: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}

    def get_market_data(self, epic: str) -> Dict:
        """Get market data for an instrument"""
        if self.use_mock:
            return self.mock_data.get_market_data() if self.mock_data else {}

        try:
            self._rate_limit()

            if self._check_token_expiry():
                if not self.login():
                    return {"error": "Failed to refresh authentication"}

            response = self.session.get(
                f"{self.base_url}/markets/{epic}",
                headers=self.get_headers(version="3"),
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                snapshot = data.get("snapshot", {})

                return {
                    "bid": float(snapshot.get("bid", 0)),
                    "offer": float(snapshot.get("offer", 0)),
                    "price": (
                        float(snapshot.get("bid", 0)) + float(snapshot.get("offer", 0))
                    )
                    / 2,
                    "high": float(snapshot.get("high", 0)),
                    "low": float(snapshot.get("low", 0)),
                    "update_time": snapshot.get("updateTime"),
                    "volatility": max(
                        0.001, abs(float(snapshot.get("percentageChange", 0.1)) / 100)
                    ),
                }

            logger.warning(f"Failed to get market data: {response.text}")
            return self.mock_data.get_market_data() if self.mock_data else {}

        except (ValueError, TypeError) as e:
            logger.error(f"Error parsing market data: {str(e)}")
            return self.mock_data.get_market_data() if self.mock_data else {}
        except Exception as e:
            logger.error(f"Error getting market data: {str(e)}")
            return self.mock_data.get_market_data() if self.mock_data else {}

    def create_position(
        self,
        direction: Union[OrderDirection, str],
        epic: str,
        size: float,
        order_type: Union[OrderType, str] = OrderType.MARKET,
    ) -> Dict:
        """Create a new position"""
        try:
            # Convert string enums if necessary
            if isinstance(direction, str):
                direction = OrderDirection(direction)
            if isinstance(order_type, str):
                order_type = OrderType(order_type)

            data = {
                "epic": epic,
                "expiry": "-",
                "direction": direction.value,
                "size": str(float(size)),  # Ensure size is valid float
                "orderType": order_type.value,
                "timeInForce": "FILL_OR_KILL",
                "guaranteedStop": False,
                "forceOpen": True,
            }

            if self._check_token_expiry():
                if not self.login():
                    return {"error": "Failed to refresh authentication"}

            response = self.session.post(
                f"{self.base_url}/positions/otc",
                headers=self.get_headers(),
                json=data,
                timeout=30,
            )

            return self._handle_response(response, "Create position")

        except ValueError as e:
            error_msg = f"Invalid parameter values: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}
        except Exception as e:
            error_msg = f"Failed to create position: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}

    def _rate_limit(self) -> None:
        """Implement rate limiting"""
        current_time = time.time()
        elapsed = current_time - self.last_request_time

        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)

        self.last_request_time = time.time()

    def get_headers(self, version: str = "2") -> Dict:
        """Get headers for API requests"""
        if not self.security_token or not self.cst:
            if not self.login():
                raise Exception("Failed to authenticate with IG API")

        return {
            "X-IG-API-KEY": self.api_key,
            "X-SECURITY-TOKEN": self.security_token,
            "CST": self.cst,
            "Version": version,
            "Content-Type": "application/json",
            "Accept": "application/json; charset=UTF-8",
        }
