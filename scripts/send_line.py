"""
send_line.py
Formats fetched Taiwan retail stock data and sends a LINE push message
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
    lines = [f"📊 台灣零售股月報 {fetched_at}", ""]

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

    # 個股月營收 + 股價
    for stock in data.get("stocks", []):
        lines.append(f"【{stock.get('name', '')} ({stock.get('co_id', '')})】")

        if stock.get("close_price") is not None:
            chg = stock.get("price_change_pct")
            chg_str = f"（{_fmt_pct(chg)}）" if chg is not None else ""
            lines.append(f"股價: {stock['close_price']:.1f} 元 {chg_str}".strip())
        if stock.get("market_cap_100m") is not None:
            lines.append(f"市值: {stock['market_cap_100m']:.1f} 億元")

        src = stock.get("revenue_source")
        src_str = f"（{src}）" if src else ""

        if stock.get("latest_month_label"):
            mom = stock.get("latest_month_mom_pct")
            yoy = stock.get("latest_month_yoy_pct")
            chg_bits = "  ".join(
                p for p in (
                    f"MoM {_fmt_pct(mom)}" if mom is not None else "",
                    f"YoY {_fmt_pct(yoy)}" if yoy is not None else "",
                ) if p
            )
            chg_bits = f"  {chg_bits}" if chg_bits else ""
            lines.append(
                f"最新月營收 ({stock['latest_month_label']}): "
                f"{_fmt_100m(stock.get('latest_month_revenue_100m'))}{chg_bits}"
            )

            recent = stock.get("recent_months") or []
            if len(recent) > 1:
                lines.append("近半年月營收:")
                for m in recent:
                    lines.append(f"· {m['label']}: {_fmt_100m(m.get('revenue_100m'))}")
        else:
            # 備援：FinMind 抓不到月資料時退回 Yahoo 季報
            quarters = stock.get("quarters") or []
            if quarters:
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

        if (
            stock.get("close_price") is None
            and not stock.get("latest_month_label")
            and not stock.get("quarters")
        ):
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
        "fetched_at": "2026/08/11 09:00",
        "cpi": {"month": "2025年（年均）", "cpi": None, "yoy_pct": 1.7, "error": None},
        "stocks": [
            {
                "name": "寶島光學科技", "co_id": "5312",
                "close_price": 88.9, "price_change_pct": -1.6, "market_cap_100m": 53.4,
                "revenue_source": "MOPS月營收(FinMind)",
                "latest_month_label": "2026年7月",
                "latest_month_revenue_100m": 3.8, "latest_month_mom_pct": 2.1, "latest_month_yoy_pct": 9.5,
                "recent_months": [
                    {"label": "2026/2", "revenue_100m": 3.5},
                    {"label": "2026/3", "revenue_100m": 3.6},
                    {"label": "2026/4", "revenue_100m": 3.7},
                    {"label": "2026/5", "revenue_100m": 3.6},
                    {"label": "2026/6", "revenue_100m": 3.7},
                    {"label": "2026/7", "revenue_100m": 3.8},
                ],
                "ytd_label": "2026/1–7月", "ytd_revenue_100m": 26.3, "ytd_yoy_pct": 12.9,
                "gross_margin_pct": 63.7, "net_income_100m": 3.5, "error": None,
            },
        ],
    }
    print(build_message(sample_data))
