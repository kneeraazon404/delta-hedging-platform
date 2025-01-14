# app/api/routes.py
import logging
from datetime import datetime

from flask import jsonify, request

from app import app
from app.core.delta_hedger import DeltaHedger
from app.services.ig_client import IGClient
from config.settings import HEDGE_SETTINGS as _hedge_settings

# Initialize clients
ig_client = IGClient(use_mock=True)
hedger = DeltaHedger(ig_client)


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
