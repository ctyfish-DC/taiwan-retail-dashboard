"""
eyewear_notify.py
Orchestrates fetching new Eyewear Intelligence headlines and pushing a
weekly round-up via LINE, reusing the same LINE push helper as main.py.

Required environment variables:
    LINE_TOKEN    — LINE channel access token
    LINE_USER_ID  — LINE user ID to push to
"""

import logging
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

STATE_PATH = "data/eyewear_state.json"


def build_message(articles: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    lines = [f"👓 Eyewear Intelligence 週報 {now}", ""]
    for a in articles:
        date = a["published_at"][:10] if a["published_at"] else ""
        lines.append(f"• {a['title']} ({date})")
        lines.append(a["url"])
        lines.append("")
    lines.append("─────────────────")
    lines.append("資料來源: ewintelligence.com")
    return "\n".join(lines)


def main() -> int:
    try:
        from fetch_eyewear import fetch_new_articles
        from send_line import send_line_message
    except ImportError as exc:
        logger.error("Import error: %s", exc)
        return 1

    logger.info("=== Eyewear Intelligence Weekly Notify — starting ===")

    articles = fetch_new_articles(STATE_PATH)
    logger.info("Found %d new article(s).", len(articles))

    if not articles:
        logger.info("No new articles since last run — skipping LINE push.")
        return 0

    message = build_message(articles)
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
