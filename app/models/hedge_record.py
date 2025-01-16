# app/models/hedge_record.py
from datetime import datetime
from typing import Dict


class HedgeRecord:
    def __init__(self, delta: float, hedge_size: float, price: float, pnl: float):
        """Initialize hedge record with validation"""
        try:
            self.timestamp = datetime.now().isoformat()
            self.delta = float(delta)
            self.hedge_size = float(hedge_size)
            self.price = float(price)
            self.pnl = float(pnl)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid hedge record data: {str(e)}")

    @classmethod
    def from_dict(cls, data: Dict) -> "HedgeRecord":
        """Create HedgeRecord from dictionary"""
        try:
            return cls(
                delta=data.get("delta", 0.0),
                hedge_size=data.get("hedge_size", 0.0),
                price=data.get("price", 0.0),
                pnl=data.get("pnl", 0.0),
            )
        except Exception as e:
            raise ValueError(f"Error creating HedgeRecord from dict: {str(e)}")

    def to_dict(self) -> Dict:
        """Convert hedge record to dictionary"""
        try:
            return {
                "timestamp": self.timestamp,
                "delta": self.delta,
                "hedge_size": self.hedge_size,
                "price": self.price,
                "pnl": self.pnl,
            }
        except Exception as e:
            raise ValueError(f"Error converting hedge record to dict: {str(e)}")

    def __str__(self) -> str:
        """String representation of hedge record"""
        return (
            f"HedgeRecord(delta={self.delta:.4f}, "
            f"hedge_size={self.hedge_size:.2f}, "
            f"price={self.price:.2f}, "
            f"pnl={self.pnl:.2f})"
        )

    def __repr__(self) -> str:
        return self.__str__()
