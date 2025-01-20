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

        # Account ID
        self.account_id = os.getenv("IG_OPTIONS_ACCOUNT")
        logger.info(f"Initializing IG Client with account ID: {self.account_id}")

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
        if not self.account_id:
            missing.append("IG_OPTIONS_ACCOUNT")

        if missing:
            raise ValueError(f"Missing IG API credentials: {', '.join(missing)}")

    def _handle_rate_limit(self, response: requests.Response) -> bool:
        """Handle rate limiting errors"""
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            logger.warning(f"Rate limit exceeded, waiting {retry_after} seconds")
            time.sleep(retry_after)
            return True

        error_code = response.json().get("errorCode", "")
        if (
            "exceeded-api-key-allowance" in error_code
            or "exceeded-account-allowance" in error_code
        ):
            logger.warning(f"API limit exceeded: {error_code}")
            time.sleep(60)  # Wait for 1 minute
            return True

        return False

    def _handle_response(self, response: requests.Response, operation: str) -> Dict:
        """Handle API response with rate limiting"""
        try:
            if self._handle_rate_limit(response):
                return {"error": "Rate limit exceeded, please try again"}

            if response.status_code in [200, 201]:
                return response.json()

            error_msg = f"{operation} failed with status {response.status_code}: {response.text}"
            logger.error(error_msg)
            return {"error": error_msg}

        except Exception as e:
            logger.error(f"Error handling response for {operation}: {str(e)}")
            return {"error": str(e)}

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
                self.token_expiry = datetime.now() + timedelta(hours=6)

                logger.info("Successfully logged in to IG API")

                # Get available accounts
                accounts_response = self.session.get(
                    f"{self.base_url}/accounts", headers=self.get_headers(), timeout=30
                )

                if accounts_response.status_code == 200:
                    accounts = accounts_response.json().get("accounts", [])
                    logger.debug(f"Available accounts: {accounts}")

                    # Find matching account
                    matching_account = next(
                        (
                            acc
                            for acc in accounts
                            if acc.get("accountId") == self.account_id
                        ),
                        None,
                    )

                    if matching_account:
                        # Set the account
                        switch_response = self.session.put(
                            f"{self.base_url}/session",
                            headers=self.get_headers(),
                            json={"accountId": self.account_id},
                            timeout=30,
                        )

                        if switch_response.status_code in [200, 204]:
                            logger.info(
                                f"Successfully set account to {self.account_id}"
                            )
                        else:
                            logger.warning(
                                f"Could not set account. Will continue with default. Status: {switch_response.status_code}"
                            )
                    else:
                        logger.warning(
                            f"Account {self.account_id} not found in available accounts. Will continue with default account."
                        )

                return True

            logger.error(f"Login failed: {response.text}")
            return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Login request error: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            return False

    def _process_position_data(self, position_data: Dict) -> Dict:
        """Process and validate position data before creating Position object"""
        try:
            # Extract market data
            market_data = position_data.get("market", {})

            # Log raw data for debugging
            logger.debug(f"Raw position data: {position_data}")

            # Check if this is a currency position
            instrument_type = market_data.get("instrumentType", "").upper()
            if instrument_type in ["CURRENCIES", "FOREX"]:
                # For currency positions, set a default CALL type
                market_data["instrumentType"] = "CALL"
                position_data["market"] = market_data
                logger.info(f"Processing currency position: {market_data.get('epic')}")
                return position_data

            # For other instruments, determine option type
            instrument_name = market_data.get("instrumentName", "")
            logger.debug(f"Processing position with instrument name: {instrument_name}")

            # Default to CALL if can't determine
            option_type = "CALL"
            if instrument_name:
                if "PUT" in instrument_name.upper():
                    option_type = "PUT"
                elif "CALL" in instrument_name.upper():
                    option_type = "CALL"

            # Update the market data
            market_data["instrumentType"] = option_type
            position_data["market"] = market_data

            return position_data

        except Exception as e:
            logger.error(f"Error processing position data: {str(e)}")
            logger.debug(f"Problematic position data: {position_data}")
            return position_data

    def get_positions(self) -> Dict:
        """Get all positions from IG API"""
        if self.use_mock:
            return {"positions": []}

        try:
            logger.info(f"Fetching positions for account: {self.account_id}")

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

            # Process each position
            processed_positions = []
            for position in positions_data.get("positions", []):
                try:
                    # Process and validate position data
                    processed_position = self._process_position_data(position)
                    processed_positions.append(processed_position)
                except Exception as e:
                    logger.error(f"Error processing position: {str(e)}")
                    continue

            positions_data["positions"] = processed_positions

            # Log position count
            position_count = len(processed_positions)
            logger.info(
                f"Found {position_count} positions for account {self.account_id}"
            )

            return positions_data

        except requests.exceptions.RequestException as e:
            error_msg = (
                f"Failed to get positions for account {self.account_id}: {str(e)}"
            )
            logger.error(error_msg)
            return {"error": error_msg}

    def get_market_data(self, epic: str) -> Dict:
        """Get market data for an instrument"""
        if self.use_mock:
            return self.mock_data.get_market_data() if self.mock_data else {}

        try:
            self._rate_limit()
            logger.debug(f"Fetching market data for {epic}")

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

                market_data = {
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

                logger.debug(f"Market data received for {epic}: {market_data}")
                return market_data

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
        epic: str,
        direction: OrderDirection,
        size: float,
        order_type: OrderType = OrderType.MARKET,
        limit_level: Optional[float] = None,
    ) -> Dict:
        """Create a new position with proper validation"""
        try:
            logger.info(
                f"Creating position on account {self.account_id}: {epic} {direction.value} {size}"
            )

            # Get current market data for price levels
            market_data = self.get_market_data(epic)
            if not market_data:
                return {"error": "Failed to get market data"}

            current_price = market_data.get("price", 0)
            logger.debug(f"Current market price for {epic}: {current_price}")

            # Try MARKET order first
            if order_type == OrderType.MARKET:
                logger.debug("Attempting MARKET order")
                # Base position data for MARKET order
                data = {
                    "epic": epic,
                    "expiry": "-",
                    "direction": direction.value,
                    "size": str(size),
                    "currencyCode": "GBP",
                    "forceOpen": True,
                    "orderType": OrderType.MARKET.value,
                    "timeInForce": "FILL_OR_KILL",
                    "guaranteedStop": False,
                }

                response = self.session.post(
                    f"{self.base_url}/positions/otc",
                    headers=self.get_headers(),
                    json=data,
                    timeout=30,
                )

                result = self._handle_response(response, "Create position")

                # If market order not supported, switch to LIMIT
                if "error" in result and "market-orders.not-supported" in result.get(
                    "error", ""
                ):
                    logger.info("Market orders not supported, switching to LIMIT order")
                    order_type = OrderType.LIMIT
                else:
                    return result

            # Handle LIMIT order
            if order_type == OrderType.LIMIT:
                logger.debug("Using LIMIT order")
                price_level = limit_level or current_price

                # Create LIMIT order data
                data = {
                    "epic": epic,
                    "expiry": "-",
                    "direction": direction.value,
                    "size": str(size),
                    "currencyCode": "GBP",
                    "forceOpen": True,
                    "orderType": OrderType.LIMIT.value,
                    "level": str(price_level),
                    "timeInForce": "FILL_OR_KILL",
                    "guaranteedStop": False,
                }

                logger.debug(f"Sending LIMIT order with price level: {price_level}")
                response = self.session.post(
                    f"{self.base_url}/positions/otc",
                    headers=self.get_headers(),
                    json=data,
                    timeout=30,
                )

                result = self._handle_response(response, "Create limit position")
                return result

            return {"error": "Unknown order type"}

        except Exception as e:
            logger.error(f"Failed to create position: {str(e)}")
            return {"error": str(e)}

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
