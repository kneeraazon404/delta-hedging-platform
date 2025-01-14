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
