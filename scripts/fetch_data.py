"""
fetch_data.py
Fetches Taiwan retail statistics from:
  - CPI: IMF DataMapper API (fallback: World Bank) — annual YoY inflation
  - 寶島光學科技 (5312, 上櫃): Yahoo Finance via yfinance

MOEA 經濟部零售業數據：台灣政府網站（moea.gov.tw / data.gov.tw / mops.twse.com.tw）
封鎖境外 IP，GitHub Actions（美國）無法抓取。待改用台灣 IP（自架 runner）後再啟用。
"""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# MOEA 經濟部統計處 — 需要台灣 IP，暫停抓取
# ─────────────────────────────────────────────

def fetch_moea_retail() -> dict:
    logger.info("MOEA 零售業數據需台灣 IP，暫不抓取（等自架 runner）")
    return {"overall": None, "error": "需台灣 IP，待自架 runner 後啟用"}


# ─────────────────────────────────────────────
# CPI — IMF DataMapper（全球可用），備援 World Bank
# ─────────────────────────────────────────────

def fetch_cpi() -> dict:
    result = {"month": None, "cpi": None, "yoy_pct": None, "error": None}

    for attempt in (_cpi_via_imf, _cpi_via_worldbank):
        try:
            parsed = attempt()
            if parsed.get("yoy_pct") is not None or parsed.get("cpi") is not None:
                result.update(parsed)
                logger.info("CPI fetched via %s", attempt.__name__)
                return result
        except Exception as exc:
            logger.warning("CPI %s failed: %s", attempt.__name__, exc)

    result["error"] = "CPI 資料暫時無法取得"
    logger.warning("All CPI fetch attempts failed")
    return result


def _cpi_via_imf() -> dict:
    """PCPIPCH = CPI annual % change for Taiwan (TWN). Free, no auth."""
    url = "https://www.imf.org/external/datamapper/api/v1/PCPIPCH/TWN"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    values = resp.json().get("values", {}).get("PCPIPCH", {}).get("TWN", {})
    if not values:
        raise ValueError("No IMF PCPIPCH data for TWN")

    # IMF includes forecast years — only use confirmed historical years
    current_year = datetime.now().year
    historical = {y: v for y, v in values.items() if int(y) < current_year and v is not None}
    if not historical:
        raise ValueError("No historical IMF data for TWN")

    latest_year = max(historical.keys(), key=lambda y: int(y))
    return {
        "month": f"{latest_year}年（年均）",
        "cpi": None,
        "yoy_pct": round(float(historical[latest_year]), 2),
    }


def _cpi_via_worldbank() -> dict:
    """FP.CPI.TOTL.ZG = CPI annual % change for Taiwan (TW)."""
    url = (
        "https://api.worldbank.org/v2/country/TW/indicator/FP.CPI.TOTL.ZG"
        "?format=json&mrv=3&per_page=3"
    )
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    body = resp.json()
    records = body[1] if len(body) > 1 else []
    for rec in records:
        if rec.get("value") is not None:
            return {
                "month": f"{rec.get('date', '')}年（年均）",
                "cpi": None,
                "yoy_pct": round(float(rec["value"]), 2),
            }
    raise ValueError("No World Bank CPI data")


# ─────────────────────────────────────────────
# 寶島光學科技 (5312, 上櫃) — yfinance
# ─────────────────────────────────────────────

BAODAO_CO_ID = "5312"
BAODAO_TICKER = f"{BAODAO_CO_ID}.TWO"  # Yahoo Finance OTC Taiwan ticker


def fetch_baodao() -> dict:
    result = {
        "period": None,
        "revenue_100m": None,
        "revenue_yoy_pct": None,
        "latest_q_label": None,
        "latest_q_revenue_100m": None,
        "latest_q_yoy_pct": None,
        "gross_margin_pct": None,
        "net_income_100m": None,
        "close_price": None,
        "price_change_pct": None,
        "week52_high": None,
        "week52_low": None,
        "market_cap_100m": None,
        "error": None,
    }

    try:
        import yfinance as yf
        ticker = yf.Ticker(BAODAO_TICKER)
        info = ticker.info

        # 股價
        result["close_price"] = info.get("currentPrice") or info.get("regularMarketPrice")
        result["week52_high"] = info.get("fiftyTwoWeekHigh")
        result["week52_low"] = info.get("fiftyTwoWeekLow")

        market_cap = info.get("marketCap")
        if market_cap:
            result["market_cap_100m"] = round(market_cap / 100_000_000, 1)

        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        if result["close_price"] and prev_close:
            result["price_change_pct"] = round(
                (result["close_price"] - prev_close) / prev_close * 100, 2
            )

        # 財務
        gross_margins = info.get("grossMargins")
        if gross_margins is not None:
            result["gross_margin_pct"] = round(gross_margins * 100, 1)

        net_income = info.get("netIncomeToCommon")
        if net_income:
            result["net_income_100m"] = round(net_income / 100_000_000, 2)

        _calc_revenue(ticker, result)

        if result["close_price"]:
            logger.info("寶島 5312 yfinance OK: price=%s", result["close_price"])
        else:
            logger.warning("寶島 5312: price not found in yfinance info")

    except Exception as exc:
        logger.warning("yfinance failed: %s", exc)
        result["error"] = str(exc)

    return result


def _calc_revenue(ticker, result: dict) -> None:
    """從季報取最新單季與年度累計營收，各附去年同期 YoY%。"""
    try:
        stmt = ticker.quarterly_income_stmt
        if stmt is None or stmt.empty:
            stmt = ticker.quarterly_financials
        if stmt is None or stmt.empty:
            logger.warning("yfinance: no quarterly financials")
            return

        revenue_row = None
        for idx in stmt.index:
            if "revenue" in str(idx).lower():
                revenue_row = stmt.loc[idx]
                break
        if revenue_row is None:
            logger.warning("yfinance: revenue row not found, index=%s", list(stmt.index)[:5])
            return

        revenue_row = revenue_row.dropna().sort_index(ascending=False)
        logger.info("yfinance quarterly revenue dates: %s",
                    [d.strftime("%Y-%m-%d") for d in revenue_row.index])

        by_year: dict = {}
        for d, v in revenue_row.items():
            by_year.setdefault(d.year, []).append((d, v))
        if not by_year:
            return

        current_year = datetime.now().year
        latest_year = max(by_year.keys())
        # Yahoo Finance 對小型上櫃股的季報更新較慢，可能落後一季以上
        label_note = f"（{current_year} 年資料待 Yahoo 更新）" if latest_year < current_year else ""

        latest_quarters = sorted(by_year[latest_year], reverse=True)  # newest first
        prev_quarters = sorted(by_year.get(latest_year - 1, []), reverse=True)

        # 最新單季 + 去年同季 YoY
        latest_q_date, latest_q_val = latest_quarters[0]
        q_num = (latest_q_date.month + 2) // 3
        result["latest_q_label"] = f"{latest_year} Q{q_num}"
        result["latest_q_revenue_100m"] = round(latest_q_val / 100_000_000, 2)

        same_q_prev = [v for d, v in prev_quarters if (d.month + 2) // 3 == q_num]
        if same_q_prev and same_q_prev[0]:
            result["latest_q_yoy_pct"] = round(
                (latest_q_val - same_q_prev[0]) / same_q_prev[0] * 100, 1
            )

        # 年度累計 + 去年同期 YoY
        year_total = sum(v for _, v in latest_quarters)
        n_q = len(latest_quarters)
        result["revenue_100m"] = round(year_total / 100_000_000, 2)
        result["period"] = f"{latest_year} Q1–Q{n_q}{label_note}"

        same_n_prev_vals = [v for _, v in prev_quarters[:n_q]]
        if len(same_n_prev_vals) == n_q:
            prev_total = sum(same_n_prev_vals)
            if prev_total:
                result["revenue_yoy_pct"] = round((year_total - prev_total) / prev_total * 100, 1)

        logger.info(
            "Revenue: latest_q=%s %.2f億 YoY=%s%%, cum=%s %.2f億 YoY=%s%%",
            result["latest_q_label"], result["latest_q_revenue_100m"],
            result["latest_q_yoy_pct"],
            result["period"], result["revenue_100m"], result["revenue_yoy_pct"],
        )

    except Exception as exc:
        logger.warning("Revenue calc failed: %s", exc)


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def fetch_all() -> dict:
    moea = fetch_moea_retail()

    logger.info("Fetching CPI (IMF API)…")
    cpi = fetch_cpi()

    logger.info("Fetching 寶島光學科技 (5312) via Yahoo Finance…")
    mops = fetch_baodao()

    return {
        "moea": moea,
        "cpi": cpi,
        "mops": mops,
        "fetched_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
    }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(fetch_all(), ensure_ascii=False, indent=2))
