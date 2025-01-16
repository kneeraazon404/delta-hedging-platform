import json
import os
from datetime import datetime
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()


class Position:
    def __init__(self, position_data: Dict):
        pos = position_data["position"]
        market = position_data["market"]

        self.deal_id = pos["dealId"]
        self.size = pos["size"]
        self.direction = pos["direction"]
        self.level = pos["level"]
        self.currency = pos["currency"]
        self.contract_size = pos["contractSize"]
        self.created_date = datetime.strptime(
            pos["createdDateUTC"], "%Y-%m-%dT%H:%M:%S"
        )

        self.instrument_name = market["instrumentName"]
        self.expiry = market["expiry"]
        self.epic = market["epic"]
        self.bid = market["bid"]
        self.offer = market["offer"]
        self.high = market["high"]
        self.low = market["low"]

        # Calculate position values
        self.total_size = self.size * self.contract_size
        self.current_value = self.total_size * (
            self.bid if self.direction == "SELL" else self.offer
        )
        self.entry_value = self.total_size * self.level

        # Calculate P&L
        if self.direction == "BUY":
            self.unrealized_pnl = (
                (self.bid - self.level) * self.total_size if self.bid > 0 else 0
            )
        else:
            self.unrealized_pnl = (
                (self.level - self.offer) * self.total_size if self.offer > 0 else 0
            )

    def to_dict(self) -> Dict:
        return {
            "deal_id": self.deal_id,
            "instrument": self.instrument_name,
            "direction": self.direction,
            "size": self.size,
            "total_size": self.total_size,
            "entry_level": self.level,
            "current_bid": self.bid,
            "current_offer": self.offer,
            "currency": self.currency,
            "expiry": self.expiry,
            "entry_value": round(self.entry_value, 2),
            "current_value": round(self.current_value, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
        }


def analyze_portfolio(positions: List[Position]) -> Dict:
    """Analyze the entire portfolio"""
    portfolio = {
        "total_positions": len(positions),
        "positions_by_direction": {
            "BUY": len([p for p in positions if p.direction == "BUY"]),
            "SELL": len([p for p in positions if p.direction == "SELL"]),
        },
        "positions_by_currency": {},
        "positions_by_market": {},
        "total_value": 0,
        "total_pnl": 0,
    }

    for position in positions:
        # Aggregate by currency
        if position.currency not in portfolio["positions_by_currency"]:
            portfolio["positions_by_currency"][position.currency] = {
                "count": 0,
                "total_value": 0,
                "total_pnl": 0,
            }
        portfolio["positions_by_currency"][position.currency]["count"] += 1
        portfolio["positions_by_currency"][position.currency][
            "total_value"
        ] += position.current_value
        portfolio["positions_by_currency"][position.currency][
            "total_pnl"
        ] += position.unrealized_pnl

        # Aggregate by market
        market_name = position.instrument_name.split()[
            1:3
        ]  # Get the market name (e.g., "Wall Street", "US Tech")
        market_key = " ".join(market_name)
        if market_key not in portfolio["positions_by_market"]:
            portfolio["positions_by_market"][market_key] = {
                "count": 0,
                "total_value": 0,
                "total_pnl": 0,
            }
        portfolio["positions_by_market"][market_key]["count"] += 1
        portfolio["positions_by_market"][market_key][
            "total_value"
        ] += position.current_value
        portfolio["positions_by_market"][market_key][
            "total_pnl"
        ] += position.unrealized_pnl

        # Add to totals
        portfolio["total_value"] += position.current_value
        portfolio["total_pnl"] += position.unrealized_pnl

    return portfolio


def get_positions(
    username: str, password: str, api_key: str, account_number: str, url: str
) -> Optional[List[Position]]:
    """
    Get positions from IG Trading API with proper error handling and type hints.
    """
    auth_headers = {
        "X-IG-API-KEY": api_key,
        "Version": "2",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
    }

    auth_data = {
        "identifier": username,
        "password": password,
    }

    try:
        # Step 1: Authentication
        auth_response = requests.post(
            f"{url}/session", headers=auth_headers, json=auth_data, timeout=30
        )
        auth_response.raise_for_status()

        # Get security tokens
        security_token = auth_response.headers.get("X-SECURITY-TOKEN")
        cst = auth_response.headers.get("CST")

        if not security_token or not cst:
            print("Error: Security tokens not received")
            return None

        # Step 2: Get positions
        position_headers = {
            **auth_headers,
            "X-SECURITY-TOKEN": security_token,
            "CST": cst,
        }

        position_response = requests.get(
            f"{url}/positions", headers=position_headers, timeout=30
        )
        position_response.raise_for_status()

        positions_data = position_response.json()

        # Step 3: Get detailed position information and create Position objects
        positions = []
        for position in positions_data.get("positions", []):
            deal_id = position["position"]["dealId"]
            position_details = requests.get(
                f"{url}/positions/{deal_id}", headers=position_headers, timeout=30
            )
            if position_details.status_code == 200:
                position["details"] = position_details.json()
            positions.append(Position(position))

        return positions

    except requests.exceptions.RequestException as e:
        print(f"Error: API request failed: {str(e)}")
        return None


if __name__ == "__main__":
    # Get positions
    positions = get_positions(
        username=os.getenv("IG_USERNAME"),  # type: ignore
        password=os.getenv("IG_PASSWORD"),  # type: ignore
        api_key=os.getenv("IG_API_KEY"),  # type: ignore
        account_number=os.getenv("IG_ACC_NUMBER"),  # type: ignore
        url="https://demo-api.ig.com/gateway/deal",
    )

    if positions:
        # Analyze portfolio
        portfolio_analysis = analyze_portfolio(positions)

        # Print detailed position information
        print("\nDetailed Position Analysis:")
        print(json.dumps([p.to_dict() for p in positions], indent=4))

        # Print portfolio summary
        print("\nPortfolio Summary:")
        print(json.dumps(portfolio_analysis, indent=4))
    else:
        print("Failed to retrieve positions")
