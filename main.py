# main.py
import logging
import os

from app import app

if __name__ == "__main__":
    logging.basicConfig(
        filename="logs/delta_hedger.log",
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
