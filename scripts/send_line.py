"""
send_line.py
Formats fetched Taiwan retail data and sends a LINE push message
via LINE Messaging API (Push Message).

Required environment variables:
    LINE_TOKEN    — LINE channel access token
    LINE_USER_ID  — LINE user ID to push to (starts with U...)
"""

import os
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _fmt_100m(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    return f"{val:.1f} 億元"


def build_message(data: dict) -> str:
    fetched_at = data.get("fetched_at", "")
    lines = [f"📊 台灣零售業週報 {fetched_at}", ""]

    # MOEA 整體零售業
    moea = data.get("moea", {})
    overall = moea.get("overall")
    moea_error = moea.get("error")

    lines.append("【整體零售業】")
    if overall:
        lines.append(f"最新月份: {overall.get('month', 'N/A')}")
        lines.append(f"月營業額: {_fmt_100m(overall.get('revenue_100m'))}")
        lines.append(f"YoY: {_fmt_pct(overall.get('yoy_pct'))}")
        lines.append(f"MoM: {_fmt_pct(overall.get('mom_pct'))}")
    else:
        lines.append("⚠️ 暫時無法取得")
    lines.append("")

    # CPI
    cpi = data.get("cpi", {})
    lines.append("【消費者物價指數 CPI】")
    if cpi.get("cpi") is not None:
        lines.append(f"最新月份: {cpi.get('month', 'N/A')}")
        lines.append(f"CPI: {cpi['cpi']:.2f}")
        lines.append(f"YoY: {_fmt_pct(cpi.get('yoy_pct'))}")
    elif cpi.get("yoy_pct") is not None:
        lines.append(f"參考期間: {cpi.get('month', 'N/A')}")
        lines.append(f"通膨率 YoY: {_fmt_pct(cpi.get('yoy_pct'))}")
    else:
        lines.append("⚠️ 暫時無法取得")
    lines.append("")

    # MOPS 寶島眼鏡
    mops = data.get("mops", {})
    lines.append("【寶島眼鏡 (2107) 最新財報】")
    if mops.get("period"):
        lines.append(f"期別: {mops['period']}")
        if mops.get("revenue_100m") is not None:
            lines.append(f"月營收: {_fmt_100m(mops.get('revenue_100m'))}")
        if mops.get("net_income_100m") is not None:
            lines.append(f"稅後淨利: {_fmt_100m(mops.get('net_income_100m'))}")
    else:
        lines.append("暫無新財報")
    lines.append("")

    lines.append("─────────────────")
    lines.append("資料來源: 經濟部統計處 / 主計總處 / MOPS")

    return "\n".join(lines)


def send_line_message(message: str, token: Optional[str] = None, user_id: Optional[str] = None) -> bool:
    """
    Send a push message via LINE Messaging API.

    Args:
        message: Text to send.
        token:   LINE channel access token (falls back to LINE_TOKEN env var).
        user_id: LINE user ID (falls back to LINE_USER_ID env var).
    """
    if token is None:
        token = os.environ.get("LINE_TOKEN", "")
    if user_id is None:
        user_id = os.environ.get("LINE_USER_ID", "")

    if not token:
        logger.error("LINE_TOKEN is not set.")
        return False
    if not user_id:
        logger.error("LINE_USER_ID is not set.")
        return False

    # LINE Messaging API text message limit is 5000 chars
    if len(message) > 4900:
        message = message[:4890] + "\n…(訊息過長，已截斷)"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "to": user_id,
        "messages": [{"type": "text", "text": message}],
    }

    try:
        resp = requests.post(LINE_PUSH_URL, headers=headers, json=body, timeout=15)
        if resp.status_code == 200:
            logger.info("LINE message sent successfully.")
            return True
        else:
            logger.error("LINE API failed: HTTP %s — %s", resp.status_code, resp.text)
            return False
    except requests.RequestException as exc:
        logger.error("LINE API request error: %s", exc)
        return False


# Keep old name as alias for main.py compatibility
send_line_notify = send_line_message


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO)
    sample_data = {
        "fetched_at": "2026/06/08 08:00",
        "moea": {
            "overall": {"month": "2026年4月", "revenue_100m": 3842.5, "yoy_pct": 3.2, "mom_pct": -1.8},
            "eyewear": {"month": "2026年4月", "revenue_100m": 28.6, "yoy_pct": 5.4},
            "error": None,
        },
        "cpi": {"month": "2026年5月", "cpi": 107.35, "yoy_pct": 2.24, "error": None},
        "mops": {"period": "2026Q1", "revenue_100m": 18.7, "net_income_100m": 1.3, "error": None},
    }
    msg = build_message(sample_data)
    print(msg)
