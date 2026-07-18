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

    lines.append("【整體零售業】")
    if overall:
        lines.append(f"最新月份: {overall.get('month', 'N/A')}")
        lines.append(f"月營業額: {_fmt_100m(overall.get('revenue_100m'))}")
        lines.append(f"YoY: {_fmt_pct(overall.get('yoy_pct'))}")
        lines.append(f"MoM: {_fmt_pct(overall.get('mom_pct'))}")
    else:
        lines.append(f"⚠️ {moea.get('error') or '暫時無法取得'}")
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

    # 個股營運狀況
    for stock in data.get("stocks", []):
        lines.append(f"【{stock.get('name', '')} ({stock.get('co_id', '')})】")

        if stock.get("close_price") is not None:
            chg = stock.get("price_change_pct")
            chg_str = f"（{_fmt_pct(chg)}）" if chg is not None else ""
            lines.append(f"股價: {stock['close_price']:.1f} 元 {chg_str}".strip())
        if stock.get("week52_high") is not None:
            lines.append(f"52週: {stock['week52_low']:.1f} – {stock['week52_high']:.1f} 元")
        if stock.get("market_cap_100m") is not None:
            lines.append(f"市值: {stock['market_cap_100m']:.1f} 億元")
        if stock.get("latest_q_label") is not None:
            q_yoy = stock.get("latest_q_yoy_pct")
            q_yoy_str = f"  YoY {_fmt_pct(q_yoy)}" if q_yoy is not None else ""
            lines.append(f"營收 ({stock['latest_q_label']}): {_fmt_100m(stock.get('latest_q_revenue_100m'))}{q_yoy_str}")
        if stock.get("revenue_100m") is not None:
            label = stock.get("period", "累計")
            yoy = stock.get("revenue_yoy_pct")
            yoy_str = f"  YoY {_fmt_pct(yoy)}" if yoy is not None else ""
            lines.append(f"累計營收 ({label}): {_fmt_100m(stock.get('revenue_100m'))}{yoy_str}")
        if stock.get("gross_margin_pct") is not None:
            lines.append(f"毛利率: {stock['gross_margin_pct']:.1f}%")
        if stock.get("net_income_100m") is not None:
            lines.append(f"稅後淨利: {_fmt_100m(stock.get('net_income_100m'))}")

        if stock.get("close_price") is None and stock.get("revenue_100m") is None:
            lines.append("暫無資料")
        lines.append("")

    lines.append("─────────────────")
    lines.append("資料來源: 經濟部統計處 / 主計總處 / Yahoo Finance")

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sample_data = {
        "fetched_at": "2026/06/14 08:00",
        "moea": {"overall": None, "error": "需台灣 IP，待自架 runner 後啟用"},
        "cpi": {"month": "2025年（年均）", "cpi": None, "yoy_pct": 1.7, "error": None},
        "stocks": [
            {
                "name": "寶島光學科技", "co_id": "5312",
                "close_price": 96.2, "price_change_pct": 0.9,
                "week52_low": 92.6, "week52_high": 160.0, "market_cap_100m": 57.8,
                "latest_q_label": "2025 Q4", "latest_q_revenue_100m": 3.8, "latest_q_yoy_pct": 5.2,
                "period": "2025 Q1–Q4", "revenue_100m": 15.1, "revenue_yoy_pct": 4.0,
                "gross_margin_pct": 63.7, "net_income_100m": 3.5, "error": None,
            },
            {
                "name": "寶利徠光學科技", "co_id": "1813",
                "close_price": 30.0, "price_change_pct": -1.2,
                "week52_low": 25.0, "week52_high": 40.0, "market_cap_100m": 20.0,
                "latest_q_label": "2025 Q4", "latest_q_revenue_100m": 1.0, "latest_q_yoy_pct": 3.0,
                "period": "2025 Q1–Q4", "revenue_100m": 4.0, "revenue_yoy_pct": 2.0,
                "gross_margin_pct": 50.0, "net_income_100m": 0.5, "error": None,
            },
        ],
    }
    print(build_message(sample_data))
