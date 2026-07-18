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
        if stock.get("market_cap_100m") is not None:
            lines.append(f"市值: {stock['market_cap_100m']:.1f} 億元")

        quarters = stock.get("quarters") or []
        if quarters:
            src = stock.get("revenue_source")
            src_str = f"（{src}）" if src else ""
            lines.append(f"近四季營收{src_str}:")
            for q in quarters:
                yoy = q.get("yoy_pct")
                yoy_str = f"  YoY {_fmt_pct(yoy)}" if yoy is not None else ""
                lines.append(f"· {q['label']}: {_fmt_100m(q.get('revenue_100m'))}{yoy_str}")
        if stock.get("ytd_revenue_100m") is not None:
            yoy = stock.get("ytd_yoy_pct")
            yoy_str = f"  YoY {_fmt_pct(yoy)}" if yoy is not None else ""
            lines.append(f"今年累計 ({stock.get('ytd_label')}): {_fmt_100m(stock.get('ytd_revenue_100m'))}{yoy_str}")

        if stock.get("gross_margin_pct") is not None:
            lines.append(f"毛利率: {stock['gross_margin_pct']:.1f}%")
        if stock.get("net_income_100m") is not None:
            lines.append(f"稅後淨利: {_fmt_100m(stock.get('net_income_100m'))}")

        if stock.get("close_price") is None and not quarters:
            lines.append("暫無資料")
        lines.append("")

    lines.append("─────────────────")
    lines.append("資料來源: 主計總處 / MOPS(FinMind) / Yahoo Finance")

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
                "close_price": 88.8, "price_change_pct": -1.3, "market_cap_100m": 53.3,
                "revenue_source": "MOPS月營收(FinMind)",
                "quarters": [
                    {"label": "2025 Q3", "revenue_100m": 3.7, "yoy_pct": 8.0},
                    {"label": "2025 Q4", "revenue_100m": 4.2, "yoy_pct": 22.7},
                    {"label": "2026 Q1", "revenue_100m": 3.9, "yoy_pct": 6.1},
                    {"label": "2026 Q2", "revenue_100m": 4.1, "yoy_pct": 5.4},
                ],
                "ytd_label": "2026/1–6月", "ytd_revenue_100m": 8.0, "ytd_yoy_pct": 5.7,
                "gross_margin_pct": 63.7, "net_income_100m": 3.5, "error": None,
            },
        ],
    }
    print(build_message(sample_data))
