# app/api/routes.py
import logging
from datetime import datetime
from http import HTTPStatus
from typing import Dict, List, Optional, Tuple, Union

from flask import Response, jsonify, render_template, request

from app import app
from app.core.delta_hedger import DeltaHedger
from app.models.position import Position
from app.services.ig_client import IGClient
from config.settings import HEDGE_SETTINGS as _hedge_settings

# Type alias for Flask responses
ApiResponse = Union[Response, Tuple[Response, int]]

logger = logging.getLogger(__name__)

# Initialize clients with proper error handling
try:
    ig_client = IGClient(use_mock=False)  # Using real client
    hedger = DeltaHedger(ig_client)
except Exception as e:
    logger.critical(f"Failed to initialize clients: {str(e)}")
    raise


def validate_json_request() -> Optional[Dict]:
    """Validate JSON request data"""
    if not request.is_json:
        raise ValueError("Request must be JSON")
    data = request.get_json()
    if not isinstance(data, dict):
        raise ValueError("Invalid JSON data structure")
    return data


@app.route("/")
def index() -> str:
    """Render main application page"""
    return render_template("index.html")


@app.route("/api/monitor/start", methods=["POST"])
def start_monitoring() -> ApiResponse:
    """Start automated position monitoring and delta hedging"""
    try:
        data = validate_json_request()

        # Get monitoring parameters with defaults
        monitor_interval = float(
            data.get("interval", _hedge_settings["hedge_interval"])
        )
        delta_threshold = float(
            data.get("delta_threshold", _hedge_settings["delta_threshold"])
        )

        # Validate parameters
        if monitor_interval <= 0:
            return (
                jsonify({"error": "Invalid monitoring interval"}),
                HTTPStatus.BAD_REQUEST,
            )
        if delta_threshold <= 0:
            return jsonify({"error": "Invalid delta threshold"}), HTTPStatus.BAD_REQUEST

        # Start monitoring with parameters
        result = hedger.start_monitoring(
            interval=monitor_interval, delta_threshold=delta_threshold
        )

        logger.info(f"Started position monitoring: {result}")
        return jsonify(result)

    except ValueError as e:
        return jsonify({"error": str(e)}), HTTPStatus.BAD_REQUEST
    except Exception as e:
        logger.error(f"Error starting monitoring: {str(e)}")
        return jsonify({"error": str(e)}), HTTPStatus.INTERNAL_SERVER_ERROR


@app.route("/api/positions", methods=["GET"])
def list_positions() -> ApiResponse:
    """Get all positions with delta calculations"""
    try:
        # Get positions from IG
        positions_response = ig_client.get_positions()
        if "error" in positions_response:
            return (
                jsonify({"error": positions_response["error"]}),
                HTTPStatus.BAD_REQUEST,
            )

        positions = positions_response.get("positions", [])
        if not positions:
            return (
                jsonify({"positions": [], "message": "No positions found"}),
                HTTPStatus.OK,
            )

        # Calculate metrics for each position
        positions_data = []
        total_delta = 0.0
        total_exposure = 0.0

        for position_data in positions:
            try:
                position = Position.from_dict(position_data)
                delta_info = hedger.calculate_position_delta(position)
                position_metrics = hedger.calculate_position_metrics(position)

                position_dict = position.to_dict()
                position_dict.update(
                    {
                        "delta": delta_info.get("delta", 0),
                        "needs_hedge": delta_info.get("needs_hedge", False),
                        "suggested_hedge": delta_info.get("suggested_hedge_size", 0),
                        "metrics": position_metrics,
                    }
                )

                positions_data.append(position_dict)
                total_delta += delta_info.get("delta", 0)
                total_exposure += position_metrics.get("exposure", 0)
            except Exception as e:
                logger.error(f"Error processing position: {str(e)}")
                continue

        # Group positions by instrument
        positions_by_instrument = {}
        for pos in positions_data:
            instrument = pos.get("epic", "unknown")
            if instrument not in positions_by_instrument:
                positions_by_instrument[instrument] = {
                    "positions": [],
                    "total_delta": 0.0,
                    "total_exposure": 0.0,
                }
            positions_by_instrument[instrument]["positions"].append(pos)
            positions_by_instrument[instrument]["total_delta"] += pos.get("delta", 0)
            positions_by_instrument[instrument]["total_exposure"] += pos.get(
                "metrics", {}
            ).get("exposure", 0)

        return jsonify(
            {
                "positions": positions_data,
                "by_instrument": positions_by_instrument,
                "portfolio_summary": {
                    "total_positions": len(positions),
                    "total_delta": total_delta,
                    "total_exposure": total_exposure,
                    "monitoring_status": hedger.get_monitoring_status(),
                },
            }
        )

    except Exception as e:
        logger.error(f"Error getting positions: {str(e)}")
        return jsonify({"error": str(e)}), HTTPStatus.INTERNAL_SERVER_ERROR


@app.route("/api/positions/<position_id>", methods=["GET"])
def get_position(position_id: str) -> ApiResponse:
    """Get detailed position information"""
    try:
        position = hedger.get_position(position_id)
        if not position:
            return jsonify({"error": "Position not found"}), HTTPStatus.NOT_FOUND

        try:
            market_data = ig_client.get_market_data(position.epic)
            if not market_data:
                return (
                    jsonify({"error": "Failed to fetch market data"}),
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )

            # Calculate metrics
            delta_info = hedger.calculate_position_delta(position)
            metrics = hedger.calculate_position_metrics(position)

            response = {
                "position": position.to_dict(),
                "market_data": market_data,
                "analysis": {"delta": delta_info, "metrics": metrics},
                "hedge_history": [h.to_dict() for h in position.hedge_history],
                "status": hedger.get_position_status(position_id),
            }

            return jsonify(response)

        except Exception as e:
            logger.error(f"Error calculating position metrics: {str(e)}")
            return (
                jsonify({"error": "Error calculating position metrics"}),
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    except Exception as e:
        logger.error(f"Error getting position {position_id}: {str(e)}")
        return jsonify({"error": str(e)}), HTTPStatus.INTERNAL_SERVER_ERROR


@app.route("/api/hedge/<position_id>", methods=["POST"])
def hedge_position(position_id: str) -> ApiResponse:
    """Manually trigger hedging for a position"""
    try:
        data = validate_json_request()
        force_hedge = bool(data.get("force", False))

        # Use hedge_size instead of custom_hedge_size
        result = hedger.hedge_position(
            position_id=position_id,
            force_hedge=force_hedge,
            hedge_size=float(data["hedge_size"]) if "hedge_size" in data else None,
        )

        if "error" in result:
            return jsonify(result), HTTPStatus.BAD_REQUEST

        # Get updated position info
        position = hedger.get_position(position_id)
        if position:
            result["updated_position"] = position.to_dict()
            result["updated_metrics"] = hedger.calculate_position_metrics(position)

        return jsonify(result)

    except ValueError as e:
        return jsonify({"error": str(e)}), HTTPStatus.BAD_REQUEST
    except Exception as e:
        logger.error(f"Error hedging position {position_id}: {str(e)}")
        return jsonify({"error": str(e)}), HTTPStatus.INTERNAL_SERVER_ERROR


@app.route("/api/hedge/status", methods=["GET"])
def get_hedge_status() -> ApiResponse:
    """Get hedging status for all positions"""
    try:
        positions_status = hedger.get_all_positions_status()
        monitoring_status = hedger.get_monitoring_status()

        positions_needing_hedge = sum(
            1 for p in positions_status.values() if p.get("needs_hedge", False)
        )

        total_exposure = sum(
            p.get("metrics", {}).get("exposure", 0) for p in positions_status.values()
        )

        return jsonify(
            {
                "positions_status": positions_status,
                "monitoring": monitoring_status,
                "summary": {
                    "total_positions": len(positions_status),
                    "positions_needing_hedge": positions_needing_hedge,
                    "total_exposure": total_exposure,
                    "last_update": datetime.now().isoformat(),
                },
            }
        )

    except Exception as e:
        logger.error(f"Error getting hedge status: {str(e)}")
        return jsonify({"error": str(e)}), HTTPStatus.INTERNAL_SERVER_ERROR


@app.route("/api/settings", methods=["GET", "POST"])
def handle_settings() -> ApiResponse:
    """Handle hedging settings"""
    try:
        if request.method == "POST":
            data = validate_json_request()
            validation_result = hedger.validate_settings(data)

            if "error" in validation_result:
                return jsonify(validation_result), HTTPStatus.BAD_REQUEST

            updated_settings = hedger.get_current_settings()
            return jsonify(
                {
                    "message": "Settings updated successfully",
                    "settings": updated_settings,
                }
            )

        # GET request
        current_settings = hedger.get_current_settings()
        monitoring_status = hedger.get_monitoring_status()

        return jsonify(
            {"settings": current_settings, "monitoring_status": monitoring_status}
        )

    except ValueError as e:
        return jsonify({"error": str(e)}), HTTPStatus.BAD_REQUEST
    except Exception as e:
        logger.error(f"Error handling settings: {str(e)}")
        return jsonify({"error": str(e)}), HTTPStatus.INTERNAL_SERVER_ERROR


@app.route("/api/analytics/<position_id>", methods=["GET"])
def get_position_analytics(position_id: str) -> ApiResponse:
    """Get detailed analytics for a position"""
    try:
        position = hedger.get_position(position_id)
        if not position:
            return jsonify({"error": "Position not found"}), HTTPStatus.NOT_FOUND

        market_data = ig_client.get_market_data(position.epic)
        if not market_data:
            return (
                jsonify({"error": "Failed to fetch market data"}),
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

        delta_info = hedger.calculate_position_delta(position)
        metrics = hedger.calculate_position_metrics(position)
        greeks = hedger.calculator.calculate_greeks(
            S=market_data["price"],
            K=position.strike,
            T=position.time_to_expiry,
            sigma=market_data.get("volatility", 0.2),
            option_type=position.option_type,
        )

        return jsonify(
            {
                "position": position.to_dict(),
                "market_data": market_data,
                "greeks": greeks,
                "delta_info": delta_info,
                "metrics": metrics,
                "hedge_history": [h.to_dict() for h in position.hedge_history],
            }
        )

    except Exception as e:
        logger.error(f"Error getting analytics for position {position_id}: {str(e)}")
        return jsonify({"error": str(e)}), HTTPStatus.INTERNAL_SERVER_ERROR
