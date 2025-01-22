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

        # e.g. "Daily US 500 6078.0 CALL" => ["US","500"] => "US 500"
        market_name = position.instrument_name.split()[1:3]
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

        portfolio["total_value"] += position.current_value
        portfolio["total_pnl"] += position.unrealized_pnl

    return portfolio


def login_ig(username: str, password: str, api_key: str, url: str) -> Optional[Dict]:
    """
    Logs in via POST /session (Version=2).
    Returns a dict containing { 'security_token': ..., 'cst': ..., 'current_account_id': ... } on success.
    """
    auth_headers = {
        "X-IG-API-KEY": api_key,
        "Version": "2",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
    }
    auth_data = {"identifier": username, "password": password}

    try:
        resp = requests.post(
            f"{url}/session", headers=auth_headers, json=auth_data, timeout=30
        )
        resp.raise_for_status()
        security_token = resp.headers.get("X-SECURITY-TOKEN")
        cst = resp.headers.get("CST")

        if not security_token or not cst:
            print("Error: Security tokens not received from login.")
            return None

        # The response body from login (Version=2) typically includes 'currentAccountId', 'accounts', etc.
        body = resp.json()
        current_account_id = body.get(
            "currentAccountId"
        )  # IG tells you which account is "current" after login

        return {
            "security_token": security_token,
            "cst": cst,
            "current_account_id": current_account_id,
        }
    except requests.RequestException as e:
        print(f"Login failed: {e}")
        return None


def switch_account_if_needed(
    security_token: str,
    cst: str,
    api_key: str,
    url: str,
    target_account: str,
    current_account: str,
) -> Optional[Dict]:
    """
    Switches to 'target_account' if it's different from 'current_account'.
    Returns updated tokens in a dict if the switch is done or does nothing if already on the same account.
    """
    if current_account == target_account:
        print(f"Already on account {current_account}, skipping account switch.")
        return {"security_token": security_token, "cst": cst}

    print(f"Switching from account '{current_account}' to '{target_account}'")

    switch_headers = {
        "X-IG-API-KEY": api_key,
        "X-SECURITY-TOKEN": security_token,
        "CST": cst,
        "Version": "1",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
    }
    switch_payload = {"accountId": target_account}

    try:
        resp = requests.put(
            f"{url}/session", headers=switch_headers, json=switch_payload, timeout=30
        )

        # If it's not 200 or 204, print debug
        if resp.status_code not in (200, 204):
            print(f"Account switch status code: {resp.status_code}")
            print(f"Account switch response text: {resp.text}")

        resp.raise_for_status()
        new_sec = resp.headers.get("X-SECURITY-TOKEN") or security_token
        new_cst = resp.headers.get("CST") or cst
        return {"security_token": new_sec, "cst": new_cst}
    except requests.RequestException as e:
        print(f"Failed to switch accounts: {e}")
        return None


def get_positions(
    security_token: str, cst: str, api_key: str, account_number: str, url: str
) -> Optional[List[Position]]:
    """
    Retrieves positions for the given account_number, using existing tokens.
    """
    headers = {
        "X-IG-API-KEY": api_key,
        "X-SECURITY-TOKEN": security_token,
        "CST": cst,
        "Version": "2",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
        "X-IG-ACCOUNT-ID": account_number,
    }
    try:
        resp = requests.get(f"{url}/positions", headers=headers, timeout=30)
        resp.raise_for_status()

        data = resp.json()
        positions_list = data.get("positions", [])

        positions = []
        for item in positions_list:
            # Optionally fetch details for each position
            deal_id = item["position"]["dealId"]
            detail_resp = requests.get(
                f"{url}/positions/{deal_id}", headers=headers, timeout=30
            )
            if detail_resp.status_code == 200:
                item["details"] = detail_resp.json()
            positions.append(Position(item))

        return positions
    except requests.RequestException as e:
        print(f"Error retrieving positions: {e}")
        return None


def create_position(
    security_token: str,
    cst: str,
    api_key: str,
    account_number: str,
    url: str,
    epic: str,
    direction: str,
    size: float,
    expiry: str = "-",
    currency: str = "GBP",
    guaranteed_stop: bool = False,
    trailing_stop: bool = False,
) -> Optional[Dict]:
    """
    Places a market order on the specified account and confirms it via GET /confirms/{dealReference}.
    """
    headers = {
        "X-IG-API-KEY": api_key,
        "X-SECURITY-TOKEN": security_token,
        "CST": cst,
        "Version": "2",
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json; charset=UTF-8",
        "X-IG-ACCOUNT-ID": account_number,
    }

    position_data = {
        "epic": epic,
        "expiry": expiry,
        "direction": direction,
        "size": str(size),
        "orderType": "MARKET",
        "currencyCode": currency,
        "forceOpen": True,
        "guaranteedStop": guaranteed_stop,
        "trailingStop": trailing_stop,
    }
    print("Creating position:", position_data)

    try:
        resp = requests.post(
            f"{url}/positions/otc", headers=headers, json=position_data, timeout=30
        )
        resp.raise_for_status()

        result = resp.json()
        print("Full response from IG (create_position):", json.dumps(result, indent=2))

        deal_ref = result.get("dealReference")
        if not deal_ref:
            print("No 'dealReference' returned - trade may be rejected.")
            print("Response keys:", list(result.keys()))
            return result

        # Confirmation typically needs Version=1
        confirm_headers = headers.copy()
        confirm_headers["Version"] = "1"

        conf = requests.get(
            f"{url}/confirms/{deal_ref}", headers=confirm_headers, timeout=30
        )
        print("Confirmation status code:", conf.status_code)
        print("Confirmation raw text:", conf.text)
        try:
            conf_json = conf.json()
            print("Parsed Confirmation JSON:", json.dumps(conf_json, indent=2))
            return conf_json
        except ValueError:
            print("Could not parse confirmation as JSON.")
            return {"error": "Non-JSON response from confirms"}
    except requests.RequestException as e:
        print(f"Error creating position: {e}")
        return None


if __name__ == "__main__":
    IG_USERNAME = os.getenv("IG_USERNAME")  # e.g. "mydemoaccount"
    IG_PASSWORD = os.getenv("IG_PASSWORD")  # e.g. "mypassword"
    IG_API_KEY = os.getenv("IG_API_KEY")  # e.g. "myapikey"
    IG_OPTIONS_ACCOUNT = os.getenv("IG_OPTIONS_ACCOUNT")  # e.g. "ABC123"
    IG_CFD_ACCOUNT = os.getenv("IG_CFD_ACCOUNT")  # e.g. "XYZ789"

    IG_BASE_URL = "https://demo-api.ig.com/gateway/deal"  # Live: "https://api.ig.com/gateway/deal"

    # 1) Validate environment variables
    for var_name in [
        "IG_USERNAME",
        "IG_PASSWORD",
        "IG_API_KEY",
        "IG_OPTIONS_ACCOUNT",
        "IG_CFD_ACCOUNT",
    ]:
        if not globals()[var_name]:
            raise ValueError(f"Missing environment variable: {var_name}")

    # 2) Log in once
    print("Logging in to IG...")
    login_data = login_ig(IG_USERNAME, IG_PASSWORD, IG_API_KEY, IG_BASE_URL)  # type: ignore
    if not login_data:
        raise SystemExit("Cannot proceed without a successful login.")

    security_token = login_data["security_token"]
    cst = login_data["cst"]
    current_acct = login_data["current_account_id"]
    print(f"Logged in. Current account from IG's response is: {current_acct}")

    # 3) Retrieve positions from the "options" account
    print(f"\nRetrieving positions for account: {IG_OPTIONS_ACCOUNT}")
    # Switch if needed
    switched_tokens = switch_account_if_needed(
        security_token, cst, IG_API_KEY, IG_BASE_URL, IG_OPTIONS_ACCOUNT, current_acct  # type: ignore
    )
    if not switched_tokens:
        print(
            "Account switch to OPTIONS account failed or not needed. Aborting positions fetch."
        )
        positions = None
    else:
        # Update tokens after switch
        security_token = switched_tokens["security_token"]
        cst = switched_tokens["cst"]
        current_acct = IG_OPTIONS_ACCOUNT  # If the switch was successful

        positions = get_positions(
            security_token, cst, IG_API_KEY, IG_OPTIONS_ACCOUNT, IG_BASE_URL  # type: ignore
        )

    if positions:
        portfolio_analysis = analyze_portfolio(positions)
        print("\nDetailed Position Analysis:")
        print(json.dumps([p.to_dict() for p in positions], indent=4))
        print("\nPortfolio Summary:")
        print(json.dumps(portfolio_analysis, indent=4))
    else:
        print("Failed to retrieve positions.")

    # 4) Create a position on the "CFD" account
    print(f"\nNow creating a position in {IG_CFD_ACCOUNT}...")
    switched_tokens = switch_account_if_needed(
        security_token, cst, IG_API_KEY, IG_BASE_URL, IG_CFD_ACCOUNT, current_acct  # type: ignore
    )
    if not switched_tokens:
        print("Account switch to CFD account failed or not needed. Cannot place trade.")
        new_position = None
    else:
        security_token = switched_tokens["security_token"]
        cst = switched_tokens["cst"]
        current_acct = IG_CFD_ACCOUNT

        new_position = create_position(
            security_token=security_token,
            cst=cst,
            api_key=IG_API_KEY,  # type: ignore
            account_number=IG_CFD_ACCOUNT,  # type: ignore
            url=IG_BASE_URL,
            epic="IX.D.SPTRD.IFS.IP",  # Example epic
            direction="BUY",
            size=1.5,
            expiry="-",  # or "DFB", "DEC-25", etc.
            currency="GBP",  # Ensure it's valid for your CFD account
        )

    print("\nResult from create_position:")
    if new_position:
        print(json.dumps(new_position, indent=4))
    else:
        print("Failed to create position or got no response.")

    # 5) Re-check positions in the CFD account
    print(f"\nRe-checking positions in {IG_CFD_ACCOUNT}...")
    cfd_positions = get_positions(
        security_token, cst, IG_API_KEY, IG_CFD_ACCOUNT, IG_BASE_URL  # type: ignore
    )
    if cfd_positions:
        print("Open CFD positions after trade attempt:")
        print(json.dumps([p.to_dict() for p in cfd_positions], indent=4))
    else:
        print("Failed to retrieve CFD positions after trade attempt.")
