import logging
from typing import Dict, Tuple

import numpy as np
from scipy.stats import norm

from app.models.enums import OptionType
from config.settings import HEDGE_SETTINGS

logger = logging.getLogger(__name__)


class OptionCalculator:
    def __init__(self, rate: float = HEDGE_SETTINGS["default_rate"]):
        self.rate = rate
        self.min_volatility = HEDGE_SETTINGS["min_volatility"]
        self.max_volatility = HEDGE_SETTINGS["max_volatility"]

    def validate_inputs(self, S: float, K: float, T: float, sigma: float) -> None:
        """Validate inputs with detailed error messages"""
        if S <= 0:
            raise ValueError(f"Stock price (S={S}) must be positive")
        if K <= 0:
            raise ValueError(f"Strike price (K={K}) must be positive")
        if T <= 0:
            raise ValueError(f"Time to expiry (T={T}) must be positive")
        if sigma <= 0:
            raise ValueError(f"Volatility (sigma={sigma}) must be positive")

    def _calculate_d1_d2(
        self, S: float, K: float, T: float, sigma: float
    ) -> Tuple[float, float]:
        """Calculate d1 and d2 parameters with better validation and fallbacks"""
        try:
            # Apply minimum values
            min_time = 0.001  # Minimum 1 day = 1/365
            min_vol = self.min_volatility
            max_vol = self.max_volatility

            T = max(T, min_time)
            sigma = max(min(sigma, max_vol), min_vol)

            logger.debug(f"Using adjusted inputs: T={T}, sigma={sigma}")

            d1 = (np.log(S / K) + (self.rate + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
            d2 = d1 - sigma * np.sqrt(T)

            return d1, d2

        except Exception as e:
            logger.error(f"Error in d1/d2 calculation: {str(e)}")
            raise

    def calculate_delta(
        self, S: float, K: float, T: float, sigma: float, option_type: OptionType
    ) -> float:
        """Calculate option delta using Black-Scholes formula"""
        try:
            self.validate_inputs(S, K, T, sigma)
            d1, _ = self._calculate_d1_d2(S, K, T, sigma)

            if option_type == OptionType.CALL:
                delta = float(norm.cdf(d1))
            else:
                delta = float(norm.cdf(d1) - 1)

            logger.debug(
                f"Delta calculated: {delta} for S={S}, K={K}, T={T}, sigma={sigma}"
            )
            return delta

        except Exception as e:
            logger.error(f"Delta calculation error: {str(e)}")
            raise

    def calculate_greeks(
        self, S: float, K: float, T: float, sigma: float, option_type: OptionType
    ) -> Dict[str, float]:
        """Calculate Greeks with proper validation and realistic values"""
        try:
            self.validate_inputs(S, K, T, sigma)

            # Input normalization
            T = max(T, 0.001)  # Minimum 1 day
            sigma = max(min(sigma, self.max_volatility), self.min_volatility)

            d1 = (np.log(S / K) + (self.rate + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
            d2 = d1 - sigma * np.sqrt(T)

            # Common terms for efficiency
            npd1 = norm.pdf(d1)
            sqrt_t = np.sqrt(T)
            exp_rt = np.exp(-self.rate * T)

            # Calculate Greeks based on option type
            if option_type == OptionType.CALL:
                delta = norm.cdf(d1)
                theta = (
                    -S * sigma * npd1 / (2 * sqrt_t)
                    - self.rate * K * exp_rt * norm.cdf(d2)
                ) / 365
                rho = K * T * exp_rt * norm.cdf(d2) / 100
            else:
                delta = norm.cdf(d1) - 1
                theta = (
                    -S * sigma * npd1 / (2 * sqrt_t)
                    + self.rate * K * exp_rt * norm.cdf(-d2)
                ) / 365
                rho = -K * T * exp_rt * norm.cdf(-d2) / 100

            # Common Greeks for both types
            gamma = npd1 / (S * sigma * sqrt_t)
            vega = S * sqrt_t * npd1 / 100

            greeks = {
                "delta": float(delta),
                "gamma": float(gamma),
                "theta": float(theta),
                "vega": float(vega),
                "rho": float(rho),
                "time_value": float(
                    S * norm.cdf(d1) - K * exp_rt * norm.cdf(d2)
                    if option_type == OptionType.CALL
                    else K * exp_rt * norm.cdf(-d2) - S * norm.cdf(-d1)
                ),
            }

            logger.debug(f"Greeks calculated: {greeks}")
            return greeks

        except Exception as e:
            logger.error(f"Error calculating Greeks: {str(e)}")
            raise

    def calculate_hedge_size(
        self, delta: float, position_size: float, min_size: float, max_size: float
    ) -> float:
        """
        Calculate required hedge size based on delta and position size
        Applies minimum and maximum constraints
        """
        try:
            if position_size <= 0:
                raise ValueError("Position size must be positive")

            hedge_size = abs(delta * position_size)

            # Apply constraints
            hedge_size = max(min_size, min(max_size, hedge_size))

            logger.debug(
                f"Calculated hedge size: {hedge_size} (delta={delta}, position_size={position_size})"
            )
            return float(hedge_size)

        except Exception as e:
            logger.error(f"Hedge size calculation error: {str(e)}")
            raise

    def calculate_implied_volatility(
        self,
        S: float,
        K: float,
        T: float,
        option_price: float,
        option_type: OptionType,
        tolerance: float = 0.0001,
        max_iterations: int = 100,
    ) -> float:
        """Calculate implied volatility using Newton-Raphson method"""
        try:
            self.validate_inputs(S, K, T, 0.1)  # Initial validation

            sigma = 0.3  # Initial guess
            for i in range(max_iterations):
                greeks = self.calculate_greeks(S, K, T, sigma, option_type)
                price = greeks["time_value"]
                vega = greeks["vega"]

                diff = option_price - price
                if abs(diff) < tolerance:
                    return sigma

                if abs(vega) < 1e-10:  # Avoid division by zero
                    sigma = sigma + 0.01
                    continue

                sigma = sigma + diff / vega
                sigma = max(min(sigma, self.max_volatility), self.min_volatility)

            raise ValueError("Implied volatility calculation did not converge")

        except Exception as e:
            logger.error(f"Implied volatility calculation error: {str(e)}")
            raise
