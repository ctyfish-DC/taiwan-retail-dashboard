"""
fetch_data.py
Fetches Taiwan retail statistics from:
  - MOEA 經濟部統計處: Overall retail monthly revenue (YoY/MoM)
  - OECD API: Taiwan CPI (globally accessible, no IP restrictions)
  - MOPS 公開資訊觀測站: 寶島光學科技 (5312) latest financial report
"""

import re
import io
import logging
from datetime import datetime
from typing import Optional

import requests
import pandas as pd
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.moea.gov.tw/",
}

# ─────────────────────────────────────────────
# MOEA 經濟部統計處 — Retail Sales (Overall only)
# ─────────────────────────────────────────────

def _make_moea_session() -> requests.Session:
    """Establish a session on MOEA main page to bypass WAF."""
    session = requests.Session()
    session.headers.update(HEADERS)
    session.headers["Referer"] = "https://www.moea.gov.tw/"
    try:
        session.get("https://www.moea.gov.tw/", timeout=15)
    except Exception:
        pass
    return session


def fetch_moea_retail() -> dict:
    result = {"overall": None, "error": None}

    for attempt in (_moea_via_datagov_search, _moea_via_datagov_direct, _moea_via_hub):
        try:
            df = attempt()
            if df is not None and not df.empty:
                parsed = _parse_moea_overall(df)
                if parsed is not None:
                    result["overall"] = parsed
                    logger.info("MOEA fetched via %s", attempt.__name__)
                    return result
                else:
                    logger.warning("MOEA %s: DataFrame found but parse failed", attempt.__name__)
        except Exception as exc:
            logger.warning("MOEA %s failed: %s", attempt.__name__, exc)

    result["error"] = "MOEA 資料暫時無法取得"
    logger.warning("All MOEA fetch attempts failed")
    return result


def _moea_via_datagov_search() -> Optional[pd.DataFrame]:
    """
    Search data.gov.tw open data for 批發零售業 dataset and download CSV/Excel.
    data.gov.tw is accessible from GitHub Actions (returns HTTP errors, not connection refused).
    """
    # Try multiple search terms
    for keyword in ("批發零售", "零售業營業額", "零售"):
        try:
            url = f"https://data.gov.tw/api/v2/datasets?keyword={requests.utils.quote(keyword)}&size=10"
            resp = requests.get(url, headers=HEADERS, timeout=20)
            logger.warning("data.gov.tw search status: %d for keyword=%s", resp.status_code, keyword)
            if resp.status_code != 200:
                continue
            body = resp.json()
            # Handle both possible response structures
            datasets = (
                body.get("result", {}).get("results")
                or body.get("results")
                or []
            )
            for ds in datasets:
                for res in ds.get("resources", []):
                    dl_url = res.get("download_url", "") or res.get("url", "")
                    if not dl_url:
                        continue
                    ext = dl_url.lower().split("?")[0].split(".")[-1]
                    if ext not in ("csv", "xlsx", "xls"):
                        continue
                    try:
                        r = requests.get(dl_url, headers=HEADERS, timeout=60)
                        r.raise_for_status()
                        if ext == "csv":
                            df = _read_csv_bytes(r.content)
                        else:
                            df = pd.read_excel(io.BytesIO(r.content), header=None)
                        if df is not None and not df.empty:
                            logger.warning("data.gov.tw: got df from %s", dl_url)
                            return df
                    except Exception as exc:
                        logger.warning("data.gov.tw download %s failed: %s", dl_url, exc)
        except Exception as exc:
            logger.warning("data.gov.tw search keyword=%s failed: %s", keyword, exc)
    raise ValueError("No usable dataset from data.gov.tw search")


def _moea_via_datagov_direct() -> Optional[pd.DataFrame]:
    """
    Try direct known data.gov.tw dataset resource URLs for MOEA retail stats.
    Dataset IDs that have historically contained this data.
    """
    # These are candidate dataset IDs — try fetching their metadata to get download URLs
    candidate_ids = ["6889", "25803", "10396"]
    for dataset_id in candidate_ids:
        try:
            meta_url = f"https://data.gov.tw/api/v2/datasets/{dataset_id}"
            resp = requests.get(meta_url, headers=HEADERS, timeout=15)
            logger.warning("data.gov.tw dataset %s: HTTP %d", dataset_id, resp.status_code)
            if resp.status_code != 200:
                continue
            resources = resp.json().get("result", {}).get("resources", [])
            for res in resources:
                dl_url = res.get("download_url", "")
                if not dl_url:
                    continue
                ext = dl_url.lower().split("?")[0].split(".")[-1]
                if ext not in ("csv", "xlsx", "xls"):
                    continue
                r = requests.get(dl_url, headers=HEADERS, timeout=60)
                r.raise_for_status()
                df = _read_csv_bytes(r.content) if ext == "csv" else pd.read_excel(io.BytesIO(r.content), header=None)
                if df is not None and not df.empty:
                    return df
        except Exception as exc:
            logger.warning("data.gov.tw dataset %s failed: %s", dataset_id, exc)
    raise ValueError("No usable direct dataset from data.gov.tw")


def _moea_via_hub() -> Optional[pd.DataFrame]:
    """Scrape MOEA statistics pages for retail data, with session warmup."""
    urls_to_try = [
        "https://www.moea.gov.tw/Mns/dos/content/wHandMenuFile.ashx?mid=9861",
        "https://www.moea.gov.tw/Mns/dos/content/Content.aspx?menu_id=9861",
        "https://www.moea.gov.tw/Mns/dos/home/IndexLink.aspx?mid=9861",
    ]
    session = _make_moea_session()

    for hub_url in urls_to_try:
        try:
            resp = session.get(hub_url, timeout=30, allow_redirects=True)
            logger.warning("MOEA hub %s → HTTP %d, %d bytes", hub_url, resp.status_code, len(resp.content))
            if resp.status_code != 200:
                continue
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            links = soup.find_all("a", href=True)
            logger.warning("MOEA hub: found %d links, first 3: %s",
                           len(links),
                           [a["href"] for a in links[:3]])

            for a in links:
                href = a["href"]
                if not href.startswith("http"):
                    href = "https://www.moea.gov.tw" + href
                # Follow any link to find Excel/CSV
                if href.endswith((".xlsx", ".xls")):
                    r = session.get(href, timeout=60)
                    r.raise_for_status()
                    df = pd.read_excel(io.BytesIO(r.content), header=None)
                    if df is not None and not df.empty:
                        logger.warning("MOEA: downloaded Excel from %s", href)
                        return df
                elif href.endswith(".csv"):
                    r = session.get(href, timeout=60)
                    r.raise_for_status()
                    df = _read_csv_bytes(r.content)
                    if df is not None and not df.empty:
                        return df
                elif "dos" in href and any(kw in a.get_text() for kw in ("零售", "批發", "統計", "下載")):
                    # Follow internal links that might lead to data
                    try:
                        r = session.get(href, timeout=20)
                        r.encoding = "utf-8"
                        sub = BeautifulSoup(r.text, "html.parser")
                        for sub_a in sub.find_all("a", href=True):
                            sub_href = sub_a["href"]
                            if not sub_href.startswith("http"):
                                sub_href = "https://www.moea.gov.tw" + sub_href
                            if sub_href.endswith((".xlsx", ".xls")):
                                r2 = session.get(sub_href, timeout=60)
                                r2.raise_for_status()
                                df = pd.read_excel(io.BytesIO(r2.content), header=None)
                                if df is not None and not df.empty:
                                    logger.warning("MOEA: found Excel via sub-link %s", sub_href)
                                    return df
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("MOEA hub %s failed: %s", hub_url, exc)

    raise ValueError("No Excel/CSV found on any MOEA hub page")


def _read_csv_bytes(content: bytes) -> Optional[pd.DataFrame]:
    for enc in ("utf-8-sig", "big5", "cp950", "utf-8"):
        try:
            return pd.read_csv(io.StringIO(content.decode(enc)))
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    return None


def _parse_moea_overall(df: pd.DataFrame) -> Optional[dict]:
    try:
        df_str = df.astype(str)
        mask = df_str.apply(lambda col: col.str.contains("零售業$", regex=True)).any(axis=1)
        row = df[mask]
        if row.empty:
            row = df.iloc[[2]]
        row = row.iloc[0]
        values = pd.to_numeric(row, errors="coerce").dropna()
        if len(values) < 2:
            return None
        rev_latest = float(values.iloc[-1])
        rev_prev = float(values.iloc[-2])
        mom = round((rev_latest - rev_prev) / rev_prev * 100, 1) if rev_prev else None
        return {
            "month": _guess_latest_month(df),
            "revenue_100m": round(rev_latest / 100, 1),
            "yoy_pct": None,
            "mom_pct": mom,
        }
    except Exception as exc:
        logger.debug("_parse_moea_overall failed: %s", exc)
        return None


def _guess_latest_month(df: pd.DataFrame) -> str:
    for cell in df.values.flatten():
        s = str(cell)
        m = re.search(r"(\d{3})(\d{2})", s)
        if m:
            roc_year, month = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12:
                return f"{roc_year + 1911}年{month}月"
        m = re.search(r"(\d{4})[年/](\d{1,2})月?", s)
        if m:
            return f"{m.group(1)}年{int(m.group(2))}月"
    now = datetime.now()
    month = now.month - 2 if now.month > 2 else now.month + 10
    year = now.year if now.month > 2 else now.year - 1
    return f"{year}年{month}月"


# ─────────────────────────────────────────────
# CPI — OECD API (globally accessible)
# ─────────────────────────────────────────────

def fetch_cpi() -> dict:
    """
    Fetch Taiwan CPI from IMF DataMapper API — globally accessible, includes Taiwan.
    Falls back to World Bank API.
    """
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
    """
    IMF DataMapper API — PCPIPCH = CPI annual % change for Taiwan (TWN).
    Returns latest available annual YoY inflation rate.
    Free, no auth, globally accessible.
    """
    url = "https://www.imf.org/external/datamapper/api/v1/PCPIPCH/TWN"
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    values = data.get("values", {}).get("PCPIPCH", {}).get("TWN", {})
    if not values:
        raise ValueError("No IMF PCPIPCH data for TWN")

    # Filter out future forecasts — only use confirmed historical years
    current_year = datetime.now().year
    historical = {y: v for y, v in values.items() if int(y) < current_year and v is not None}
    if not historical:
        raise ValueError("No historical IMF data for TWN")

    latest_year = max(historical.keys(), key=lambda y: int(y))
    yoy = round(float(historical[latest_year]), 2)

    return {
        "month": f"{latest_year}年（年均）",
        "cpi": None,
        "yoy_pct": yoy,
    }


def _cpi_via_worldbank() -> dict:
    """
    World Bank API — FP.CPI.TOTL.ZG = CPI annual % change for Taiwan (TW).
    """
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
            yoy = round(float(rec["value"]), 2)
            year = rec.get("date", "")
            return {
                "month": f"{year}年（年均）",
                "cpi": None,
                "yoy_pct": yoy,
            }
    raise ValueError("No World Bank CPI data")


# ─────────────────────────────────────────────
# 寶島光學科技 (5312, 上櫃) — yfinance
# ─────────────────────────────────────────────

BAODAO_CO_ID = "5312"
BAODAO_TICKER = f"{BAODAO_CO_ID}.TWO"  # Yahoo Finance OTC Taiwan ticker

BAODAO_TICKER = f"{BAODAO_CO_ID}.TWO"  # Yahoo Finance OTC Taiwan ticker


def fetch_mops_baodao() -> dict:
    result = {
        "period": None,
        "revenue_100m": None,
        "gross_margin_pct": None,
        "net_income_100m": None,
        "close_price": None,
        "price_change_pct": None,
        "week52_high": None,
        "week52_low": None,
        "market_cap_100m": None,
        "revenue_yoy_pct": None,
        "error": None,
    }

    try:
        import yfinance as yf
        ticker = yf.Ticker(BAODAO_TICKER)

        # 股價資訊
        info = ticker.info
        logger.warning("yfinance info keys sample: %s", list(info.keys())[:10])

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

        # 財務數據
        gross_margins = info.get("grossMargins")
        if gross_margins is not None:
            result["gross_margin_pct"] = round(gross_margins * 100, 1)

        net_income = info.get("netIncomeToCommon")
        if net_income:
            result["net_income_100m"] = round(net_income / 100_000_000, 2)

        # YTD 累計營收 + YoY（從季報加總）
        _calc_ytd_revenue(ticker, result)

        if result["close_price"]:
            logger.info("寶島 5312 yfinance OK: price=%s", result["close_price"])
        else:
            logger.warning("寶島 5312: price not found in yfinance info")

    except Exception as exc:
        logger.warning("yfinance failed: %s", exc)
        result["error"] = str(exc)

    return result


def _calc_ytd_revenue(ticker, result: dict) -> None:
    """從季報計算 YTD 累計營收及 YoY%。"""
    try:
        import yfinance as yf
        # quarterly_income_stmt: columns = quarter-end dates, rows = line items
        stmt = ticker.quarterly_income_stmt
        if stmt is None or stmt.empty:
            # fallback to older API
            stmt = ticker.quarterly_financials
        if stmt is None or stmt.empty:
            logger.warning("yfinance: no quarterly financials")
            return

        # Find revenue row
        revenue_row = None
        for idx in stmt.index:
            if "revenue" in str(idx).lower() or "total revenue" in str(idx).lower():
                revenue_row = stmt.loc[idx]
                break
        if revenue_row is None:
            logger.warning("yfinance: revenue row not found, index=%s", list(stmt.index)[:5])
            return

        # Sort columns (dates) descending
        revenue_row = revenue_row.dropna().sort_index(ascending=False)
        logger.warning("yfinance quarterly revenue dates: %s", list(revenue_row.index))

        now = datetime.now()
        current_year = now.year

        # Sum quarters in current year
        ytd_quarters = [(d, v) for d, v in revenue_row.items() if d.year == current_year]
        prev_quarters = [(d, v) for d, v in revenue_row.items() if d.year == current_year - 1]

        if not ytd_quarters:
            # No current year data yet, use previous year YTD
            ytd_quarters = prev_quarters
            prev_quarters = [(d, v) for d, v in revenue_row.items() if d.year == current_year - 2]
            label_year = current_year - 1
        else:
            label_year = current_year

        if not ytd_quarters:
            return

        n_quarters = len(ytd_quarters)
        ytd_total = sum(v for _, v in ytd_quarters)
        result["revenue_100m"] = round(ytd_total / 100_000_000, 2)
        result["period"] = f"{label_year} Q1–Q{n_quarters} YTD"

        # YoY: same number of quarters from previous year
        same_n_prev = [v for _, v in sorted(prev_quarters, reverse=True)[:n_quarters]]
        if len(same_n_prev) == n_quarters:
            prev_total = sum(same_n_prev)
            if prev_total:
                result["revenue_yoy_pct"] = round((ytd_total - prev_total) / prev_total * 100, 1)

        logger.info("YTD revenue: %s %s億, YoY=%s%%",
                    result["period"], result["revenue_100m"],
                    result.get("revenue_yoy_pct"))

    except Exception as exc:
        logger.warning("YTD revenue calc failed: %s", exc)


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def fetch_all() -> dict:
    logger.info("Fetching MOEA retail data…")
    moea = fetch_moea_retail()

    logger.info("Fetching CPI data (OECD API)…")
    cpi = fetch_cpi()

    logger.info("Fetching MOPS 寶島光學科技 (5312) data…")
    mops = fetch_mops_baodao()

    return {
        "moea": moea,
        "cpi": cpi,
        "mops": mops,
        "fetched_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
    }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    data = fetch_all()
    print(json.dumps(data, ensure_ascii=False, indent=2))
