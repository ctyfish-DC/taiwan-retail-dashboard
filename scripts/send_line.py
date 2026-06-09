"""
send_line.py
Formats fetched Taiwan retail data and sends a LINE Notify push message.

Required environment variable:
    LINE_TOKEN  — LINE Notify access token

LINE Notify API: POST https://notify-api.line.me/api/notify
"""

import os
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LINE_NOTIFY_URL = "https://notify-api.line.me/api/notify"


# ─────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────

def _fmt_pct(val: Optional[float], prefix: bool = True) -> str:
    """Format a percentage value with sign."""
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _fmt_100m(val: Optional[float]) -> str:
    """Format a value in 億元."""
    if val is None:
        return "N/A"
    return f"{val:.1f} 億元"


def build_message(data: dict) -> str:
    """
    Build the formatted LINE push message from the combined data dict
    returned by fetch_data.fetch_all().
    """
    fetched_at = data.get("fetched_at", "")
    lines = [
        f"📊 台灣零售業週報 {fetched_at}",
        "",
    ]

    # ── MOEA 整體零售業 ───────────────────────────
    moea = data.get("moea", {})
    moea_error = moea.get("error")
    overall = moea.get("overall")
    eyewear = moea.get("eyewear")

    lines.append("【整體零售業】")
    if moea_error and overall is None:
        lines.append(f"⚠️ 資料暫時無法取得: {moea_error}")
    elif overall:
        lines.append(f"最新月份: {overall.get('month', 'N/A')}")
        lines.append(f"月營業額: {_fmt_100m(overall.get('revenue_100m'))}")
        lines.append(f"YoY: {_fmt_pct(overall.get('yoy_pct'))}")
        lines.append(f"MoM: {_fmt_pct(overall.get('mom_pct'))}")
    else:
        lines.append("⚠️ 整體零售業資料暫時無法取得")
    lines.append("")

    # ── MOEA 眼鏡行 ──────────────────────────────
    lines.append("【眼鏡行類別】")
    if moea_error and eyewear is None:
        lines.append(f"⚠️ 資料暫時無法取得: {moea_error}")
    elif eyewear:
        lines.append(f"最新月份: {eyewear.get('month', 'N/A')}")
        lines.append(f"月營業額: {_fmt_100m(eyewear.get('revenue_100m'))}")
        lines.append(f"YoY: {_fmt_pct(eyewear.get('yoy_pct'))}")
    else:
        lines.append("⚠️ 眼鏡行資料暫時無法取得")
    lines.append("")

    # ── CPI 消費者物價指數 ─────────────────────────
    cpi = data.get("cpi", {})
    lines.append("【消費者物價指數 CPI】")
    if cpi.get("error") and cpi.get("cpi") is None:
        lines.append(f"⚠️ 資料暫時無法取得: {cpi['error']}")
    elif cpi.get("cpi") is not None:
        lines.append(f"最新月份: {cpi.get('month', 'N/A')}")
        lines.append(f"CPI: {cpi['cpi']:.2f}")
        lines.append(f"YoY: {_fmt_pct(cpi.get('yoy_pct'))}")
    else:
        lines.append("⚠️ CPI 資料暫時無法取得")
    lines.append("")

    # ── MOPS 寶島光學科技 (5312) ──────────────────
    mops = data.get("mops", {})
    lines.append("【寶島光學科技 (5312) 最新財報】")
    if mops.get("error") and mops.get("period") is None:
        lines.append("暫無新財報")
    elif mops.get("period"):
        lines.append(f"期別: {mops['period']}")
        if mops.get("revenue_100m") is not None:
            lines.append(f"月營收: {_fmt_100m(mops.get('revenue_100m'))}")
        if mops.get("gross_margin_pct") is not None:
            lines.append(f"毛利率: {mops['gross_margin_pct']:.1f}%")
        if mops.get("net_income_100m") is not None:
            lines.append(f"稅後淨利: {_fmt_100m(mops.get('net_income_100m'))}")
    else:
        lines.append("暫無新財報")
    lines.append("")

    # ── Footer ────────────────────────────────────
    lines.append("─────────────────")
    lines.append("資料來源: 經濟部統計處 / 主計總處 / MOPS")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# LINE Notify sender
# ─────────────────────────────────────────────

def send_line_notify(message: str, token: Optional[str] = None) -> bool:
    """
    Send a message via LINE Notify API.

    Args:
        message: The text to send (max ~1000 chars before truncation).
        token:   LINE Notify token. Falls back to LINE_TOKEN env var.

    Returns:
        True on success, False on failure.
    """
    if token is None:
        token = os.environ.get("LINE_TOKEN", "")

    if not token:
        logger.error("LINE_TOKEN is not set. Message not sent.")
        return False

    # LINE Notify has a ~1000-character limit; truncate gracefully.
    if len(message) > 1000:
        message = message[:990] + "\n…(訊息過長，已截斷)"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    payload = {"message": message}

    try:
        resp = requests.post(
            LINE_NOTIFY_URL,
            headers=headers,
            data=payload,
            timeout=15,
        )
        if resp.status_code == 200:
            logger.info("LINE Notify sent successfully.")
            return True
        else:
            logger.error(
                "LINE Notify failed: HTTP %s — %s",
                resp.status_code,
                resp.text,
            )
            return False
    except requests.RequestException as exc:
        logger.error("LINE Notify request error: %s", exc)
        return False


# ─────────────────────────────────────────────
# Standalone test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO)

    # Accept a JSON file path as argument for testing
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            sample_data = json.load(f)
    else:
        # Minimal stub for quick preview
        sample_data = {
            "fetched_at": "2024/06/03 08:00",
            "moea": {
                "overall": {
                    "month": "2024年4月",
                    "revenue_100m": 3842.5,
                    "yoy_pct": 3.2,
                    "mom_pct": -1.8,
                },
                "eyewear": {
                    "month": "2024年4月",
                    "revenue_100m": 28.6,
                    "yoy_pct": 5.4,
                },
                "error": None,
            },
            "cpi": {
                "month": "2024年5月",
                "cpi": 107.35,
                "yoy_pct": 2.24,
                "error": None,
            },
            "mops": {
                "period": "2024Q1",
                "revenue_100m": 18.7,
                "gross_profit_100m": 10.2,
                "net_income_100m": 1.3,
                "error": None,
            },
        }

    msg = build_message(sample_data)
    print("=== Preview ===")
    print(msg)
    print("===============")

    # Uncomment to actually send:
    # send_line_notify(msg)
