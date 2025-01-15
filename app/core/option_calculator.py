import logging
from typing import Dict, Tuple

import numpy as np
from scipy.stats import norm

from app.models.enums import OptionType
from config.settings import HEDGE_SETTINGS


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
        """Calculate d1 and d2 parameters for Black-Scholes"""
        epsilon = 1e-10
        T = max(T, epsilon)
        sigma = max(sigma, self.min_volatility)
        sigma = min(sigma, self.max_volatility)

        d1 = (np.log(S / K) + (self.rate + sigma**2 / 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)

        return d1, d2

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
        """
        Calculate all Greeks for monitoring
        Returns delta, gamma, theta, vega, and rho
        """
        try:
            self.validate_inputs(S, K, T, sigma)
            d1, d2 = self._calculate_d1_d2(S, K, T, sigma)

            # Calculate common terms
            sqrt_t = np.sqrt(T)
            npd1 = norm.pdf(d1)
            ncdf_d1 = norm.cdf(d1)
            ncdf_d2 = norm.cdf(d2)

            # Delta
            if option_type == OptionType.CALL:
                delta = ncdf_d1
            else:
                delta = ncdf_d1 - 1

            # Other Greeks
            gamma = npd1 / (S * sigma * sqrt_t)

            # Theta (time decay)
            theta_term1 = -(S * sigma * npd1) / (2 * sqrt_t)
            if option_type == OptionType.CALL:
                theta = theta_term1 - self.rate * K * np.exp(-self.rate * T) * ncdf_d2
            else:
                theta = theta_term1 + self.rate * K * np.exp(-self.rate * T) * (
                    1 - ncdf_d2
                )

            # Vega (volatility sensitivity)
            vega = S * sqrt_t * npd1

            # Rho (interest rate sensitivity)
            if option_type == OptionType.CALL:
                rho = K * T * np.exp(-self.rate * T) * ncdf_d2
            else:
                rho = -K * T * np.exp(-self.rate * T) * (1 - ncdf_d2)

            greeks = {
                "delta": float(delta),
                "gamma": float(gamma),
                "theta": float(theta),
                "vega": float(vega),
                "rho": float(rho),
            }

            logging.info(f"Greeks calculated: {greeks}")
            return greeks

        except Exception as e:
            logging.error(f"Greeks calculation error: {str(e)}")
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
