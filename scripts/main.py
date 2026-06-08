"""
main.py
Orchestrates data fetching and LINE push for Taiwan retail weekly report.

Required environment variables:
    LINE_TOKEN    — LINE channel access token
    LINE_USER_ID  — LINE user ID to push to
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> int:
    try:
        from fetch_data import fetch_all
        from send_line import build_message, send_line_message
    except ImportError as exc:
        logger.error("Import error: %s", exc)
        return 1

    logger.info("=== Taiwan Retail Weekly Notify — starting ===")

    data = fetch_all()
    logger.info("Data fetch complete.")

    message = build_message(data)
    logger.info("Message built (%d chars).", len(message))

    print("\n=== Message Preview ===")
    print(message)
    print("=======================\n")

    success = send_line_message(message)
    if success:
        logger.info("LINE message sent successfully.")
        return 0
    else:
        logger.error("Failed to send LINE message.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
