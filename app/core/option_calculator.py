# app/core/option_calculator.py
import logging

import numpy as np
from scipy.stats import norm

from app.models.enums import OptionType
from config.settings import HEDGE_SETTINGS


class OptionCalculator:
    def __init__(self, rate: float = HEDGE_SETTINGS["default_rate"]):
        self.rate = rate

    def validate_inputs(self, S: float, K: float, T: float, sigma: float):
        if S <= 0:
            raise ValueError("Stock price (S) must be positive")
        if K <= 0:
            raise ValueError("Strike price (K) must be positive")
        if T <= 0:
            raise ValueError("Time to expiry (T) must be positive")
        if sigma <= 0:
            raise ValueError("Volatility (sigma) must be positive")

    def calculate_delta(
        self, S: float, K: float, T: float, sigma: float, option_type: OptionType
    ) -> float:
        try:
            self.validate_inputs(S, K, T, sigma)

            epsilon = 1e-10
            T = max(T, epsilon)
            sigma = max(sigma, epsilon)

            d1 = (np.log(S / K) + (self.rate + sigma**2 / 2) * T) / (sigma * np.sqrt(T))

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
