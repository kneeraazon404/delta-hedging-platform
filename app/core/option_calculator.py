# option_calculator.py

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

    def validate_inputs(self, S: float, K: float, T: float, sigma: float):
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
            min_vol = 0.05  # Minimum 5% volatility

            T = max(T, min_time)  # Ensure reasonable time value
            sigma = max(sigma, min_vol)  # Use minimum volatility

            logger.info(f"Using adjusted inputs: T={T}, sigma={sigma}")

            d1 = (np.log(S / K) + (self.rate + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
            d2 = d1 - sigma * np.sqrt(T)

            logger.debug(f"d1={d1}, d2={d2}")
            return d1, d2

        except Exception as e:
            logger.error(f"Error in d1/d2 calculation: {str(e)}")
            raise

    def calculate_delta(
        self, S: float, K: float, T: float, sigma: float, option_type: OptionType
    ) -> float:
        """
        Calculate option delta using Black-Scholes formula
        For sold options, delta needs to be reversed for hedging
        """
        try:
            self.validate_inputs(S, K, T, sigma)
            d1, _ = self._calculate_d1_d2(S, K, T, sigma)

            if option_type == OptionType.CALL:
                delta = float(norm.cdf(d1))
            else:
                delta = float(norm.cdf(d1) - 1)

            logging.info(
                f"Delta calculated: {delta} for S={S}, K={K}, T={T}, sigma={sigma}"
            )
            return delta

        except Exception as e:
            logging.error(f"Delta calculation error: {str(e)}")
            raise

    def calculate_greeks(
        self, S: float, K: float, T: float, sigma: float, option_type: OptionType
    ) -> Dict[str, float]:
        """Calculate Greeks with proper validation and realistic values"""
        try:
            # Better input validation with minimum values
            if S <= 0 or K <= 0:
                raise ValueError("Stock and strike prices must be positive")

            # Normalize time to expiry - minimum 1 day
            T = max(T, 1 / 365)

            # Use realistic volatility range (5% - 100%)
            sigma = max(min(sigma, 1.0), 0.05)

            # Calculate d1 and d2
            d1 = (np.log(S / K) + (self.rate + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
            d2 = d1 - sigma * np.sqrt(T)

            # Common terms
            sqrt_t = np.sqrt(T)
            npd1 = norm.pdf(d1)
            exp_rt = np.exp(-self.rate * T)

            # Delta calculation
            if option_type == OptionType.CALL:
                delta = norm.cdf(d1)
            else:
                delta = norm.cdf(d1) - 1

            # Gamma calculation
            gamma = npd1 / (S * sigma * sqrt_t)

            # Theta calculation
            theta_time = -(S * sigma * npd1) / (2 * sqrt_t)
            if option_type == OptionType.CALL:
                theta = theta_time - self.rate * K * exp_rt * norm.cdf(d2)
            else:
                theta = theta_time + self.rate * K * exp_rt * norm.cdf(-d2)
            theta = theta / 365  # Convert to daily decay

            # Vega calculation (in percentage terms)
            vega = S * sqrt_t * npd1 / 100

            # Rho calculation (in percentage terms)
            if option_type == OptionType.CALL:
                rho = K * T * exp_rt * norm.cdf(d2) / 100
            else:
                rho = -K * T * exp_rt * norm.cdf(-d2) / 100

            greeks = {
                "delta": float(delta),
                "gamma": float(gamma),
                "theta": float(theta),
                "vega": float(vega),
                "rho": float(rho),
            }

            logger.info(f"Greeks calculated: {greeks}")
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
            hedge_size = abs(delta * position_size)
            return max(min_size, min(max_size, hedge_size))

        except Exception as e:
            logging.error(f"Hedge size calculation error: {str(e)}")
            raise
