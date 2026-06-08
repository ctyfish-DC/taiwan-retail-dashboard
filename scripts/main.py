"""
main.py
Orchestrates data fetching and LINE Notify push for Taiwan retail weekly report.

Usage:
    python scripts/main.py

Required environment variable:
    LINE_TOKEN  — LINE Notify access token
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
    """Fetch all data, build message, send via LINE Notify. Returns exit code."""
    # Import here so errors surface clearly
    try:
        from fetch_data import fetch_all
        from send_line import build_message, send_line_notify
    except ImportError as exc:
        logger.error("Import error — make sure dependencies are installed: %s", exc)
        return 1

    logger.info("=== Taiwan Retail Weekly Notify — starting ===")

    # ── Step 1: Fetch data ────────────────────────────────────────────────────
    logger.info("Fetching data from all sources…")
    data = fetch_all()
    logger.info("Data fetch complete.")

    # ── Step 2: Build message ─────────────────────────────────────────────────
    message = build_message(data)
    logger.info("Message built (%d chars).", len(message))

    # Print preview to stdout (visible in GitHub Actions logs)
    print("\n=== Message Preview ===")
    print(message)
    print("=======================\n")

    # ── Step 3: Send LINE Notify ──────────────────────────────────────────────
    success = send_line_notify(message)
    if success:
        logger.info("LINE Notify sent successfully.")
        return 0
    else:
        logger.error("Failed to send LINE Notify message.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
