import json
import os
from typing import Dict, Optional

import requests
from dotenv import load_dotenv

load_dotenv()


def get_positions(
    username: str, password: str, api_key: str, account_number: str, url: str
) -> Dict:
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
            return {"error": "Security tokens not received"}

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
        print(positions_data)

        # Step 3: Get detailed position information for each position
        for position in positions_data.get("positions", []):
            deal_id = position["position"]["dealId"]  # Correct nested path
            position_details = requests.get(
                f"{url}/positions/{deal_id}", headers=position_headers, timeout=30
            )
            if position_details.status_code == 200:
                # Add detailed information to the position data
                position["details"] = position_details.json()

        return positions_data

    except requests.exceptions.RequestException as e:
        return {"error": f"API request failed: {str(e)}"}


def parse_position_data(position_data: Dict) -> Dict:
    """
    Parse and extract relevant information from position data.
    """
    positions = []
    for pos in position_data.get("positions", []):
        position_info = pos["position"]
        market_info = pos["market"]

        parsed_position = {
            "deal_id": position_info["dealId"],
            "size": position_info["size"],
            "direction": position_info["direction"],
            "level": position_info["level"],
            "currency": position_info["currency"],
            "instrument_name": market_info["instrumentName"],
            "expiry": market_info["expiry"],
            "current_bid": market_info["bid"],
            "current_offer": market_info["offer"],
            "market_status": market_info["marketStatus"],
        }
        positions.append(parsed_position)

    return {"positions": positions}


if __name__ == "__main__":
    response = get_positions(
        username=os.getenv("IG_USERNAME"),  # type: ignore
        password=os.getenv("IG_PASSWORD"),  # type: ignore
        api_key=os.getenv("IG_API_KEY"),  # type: ignore
        account_number=os.getenv("IG_ACC_NUMBER"),  # type: ignore
        url="https://demo-api.ig.com/gateway/deal",
    )

    # Print full response
    print("Full response:")
    print(json.dumps(response, indent=4))

    # Print parsed data
    parsed_data = parse_position_data(response)
    print("\nParsed positions:")
    print(json.dumps(parsed_data, indent=4))
