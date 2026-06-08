"""
fetch_data.py
Fetches Taiwan retail statistics from:
  - MOEA 經濟部統計處: Overall retail monthly revenue (YoY/MoM)
  - OECD API: Taiwan CPI (globally accessible, no IP restrictions)
  - MOPS 公開資訊觀測站: 寶島眼鏡 (2107) latest financial report
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

def fetch_moea_retail() -> dict:
    result = {"overall": None, "error": None}

    for attempt in (_moea_via_dmz, _moea_via_hub, _moea_via_datagov):
        try:
            df = attempt()
            if df is not None and not df.empty:
                parsed = _parse_moea_overall(df)
                if parsed is not None:
                    result["overall"] = parsed
                    logger.info("MOEA fetched via %s", attempt.__name__)
                    return result
        except Exception as exc:
            logger.debug("MOEA %s failed: %s", attempt.__name__, exc)

    result["error"] = "MOEA 資料暫時無法取得"
    logger.warning("All MOEA fetch attempts failed")
    return result


def _moea_via_dmz() -> Optional[pd.DataFrame]:
    """POST to MOEA DMZ statistics interface."""
    session = requests.Session()
    session.headers.update(HEADERS)

    url = "https://dmz26.moea.gov.tw/GMWeb/investigate/InvestigateDA.aspx"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    viewstate = soup.find("input", {"id": "__VIEWSTATE"})
    eventval = soup.find("input", {"id": "__EVENTVALIDATION"})

    payload = {
        "__VIEWSTATE": viewstate["value"] if viewstate else "",
        "__EVENTVALIDATION": eventval["value"] if eventval else "",
        "ctl00$ContentPlaceHolder1$btnQuery": "查詢",
    }
    resp2 = session.post(url, data=payload, timeout=30)
    resp2.encoding = "utf-8"

    tables = pd.read_html(resp2.text)
    for df in tables:
        if "零售" in df.to_string():
            return df
    raise ValueError("No retail table in MOEA DMZ response")


def _moea_via_hub() -> Optional[pd.DataFrame]:
    """Scrape MOEA hub page for Excel download link."""
    session = requests.Session()
    session.headers.update(HEADERS)

    hub_url = "https://www.moea.gov.tw/Mns/dos/content/wHandMenuFile.ashx?mid=9861"
    resp = session.get(hub_url, timeout=30)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            href = "https://www.moea.gov.tw" + href
        if href.endswith((".xlsx", ".xls")):
            r = session.get(href, timeout=60)
            r.raise_for_status()
            df = pd.read_excel(io.BytesIO(r.content), header=None)
            if "零售" in df.to_string():
                return df
        elif href.endswith(".csv"):
            r = session.get(href, timeout=60)
            r.raise_for_status()
            for enc in ("utf-8-sig", "big5", "cp950"):
                try:
                    df = pd.read_csv(io.StringIO(r.content.decode(enc)))
                    if not df.empty:
                        return df
                except Exception:
                    continue
    raise ValueError("No Excel/CSV on MOEA hub page")


def _moea_via_datagov() -> Optional[pd.DataFrame]:
    """Try data.gov.tw keyword search."""
    search_url = "https://data.gov.tw/api/v2/datasets?keyword=零售業營業額&size=5&_format=json"
    resp = requests.get(search_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    body = resp.json()
    results = body.get("result", {}).get("results", body.get("results", []))
    for ds in results:
        for res in ds.get("resources", []):
            url = res.get("download_url", "")
            if not url:
                continue
            try:
                r = requests.get(url, headers=HEADERS, timeout=60)
                r.raise_for_status()
                if url.lower().endswith((".xlsx", ".xls")):
                    df = pd.read_excel(io.BytesIO(r.content), header=None)
                    if "零售" in df.to_string():
                        return df
                elif url.lower().endswith(".csv"):
                    for enc in ("utf-8-sig", "big5", "cp950"):
                        try:
                            df = pd.read_csv(io.StringIO(r.content.decode(enc)))
                            if not df.empty:
                                return df
                        except Exception:
                            continue
            except Exception as exc:
                logger.debug("data.gov.tw resource %s failed: %s", url, exc)
    raise ValueError("No usable retail dataset from data.gov.tw")


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
    Fetch Taiwan CPI from OECD Stats API — no IP restrictions.
    Falls back to World Bank API if OECD is unavailable.
    """
    result = {"month": None, "cpi": None, "yoy_pct": None, "error": None}

    for attempt in (_cpi_via_oecd, _cpi_via_worldbank):
        try:
            parsed = attempt()
            if parsed.get("cpi") is not None or parsed.get("yoy_pct") is not None:
                result.update(parsed)
                logger.info("CPI fetched via %s", attempt.__name__)
                return result
        except Exception as exc:
            logger.debug("CPI %s failed: %s", attempt.__name__, exc)

    result["error"] = "CPI 資料暫時無法取得"
    logger.warning("All CPI fetch attempts failed")
    return result


def _cpi_via_oecd() -> dict:
    """
    OECD SDMX-JSON API for Taiwan monthly CPI index (base=2015).
    Endpoint: PRICES_CPI / TWN.CPALTT01.IXOB.M
    """
    now = datetime.now()
    start = f"{now.year - 2}-01"
    url = (
        "https://stats.oecd.org/SDMX-JSON/data/"
        f"PRICES_CPI/TWN.CPALTT01.IXOB.M/all"
        f"?startTime={start}&format=json"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Navigate SDMX-JSON structure
    dataset = data.get("dataSets", [{}])[0]
    series = dataset.get("series", {})
    if not series:
        raise ValueError("No series in OECD response")

    # Get the first (only) series
    obs = next(iter(series.values())).get("observations", {})
    if not obs:
        raise ValueError("No observations in OECD series")

    # Get time dimension
    structure = data.get("structure", {})
    dims = structure.get("dimensions", {}).get("observation", [])
    time_dim = next((d for d in dims if d.get("id") == "TIME_PERIOD"), None)
    if not time_dim:
        raise ValueError("No TIME_PERIOD dimension")

    time_values = [v.get("id") for v in time_dim.get("values", [])]

    # Build sorted list of (time, value)
    obs_list = []
    for idx_str, val_list in obs.items():
        idx = int(idx_str)
        if idx < len(time_values) and val_list and val_list[0] is not None:
            obs_list.append((time_values[idx], float(val_list[0])))

    obs_list.sort(key=lambda x: x[0])
    if not obs_list:
        raise ValueError("Empty observation list")

    latest_time, latest_cpi = obs_list[-1]
    # YoY: 12 months ago
    yoy = None
    if len(obs_list) >= 13:
        prev_time, prev_cpi = obs_list[-13]
        yoy = round((latest_cpi - prev_cpi) / prev_cpi * 100, 2)

    # Parse "2026-04" → "2026年4月"
    m = re.match(r"(\d{4})-(\d{2})", latest_time)
    month_label = f"{m.group(1)}年{int(m.group(2))}月" if m else latest_time

    return {"month": month_label, "cpi": round(latest_cpi, 2), "yoy_pct": yoy}


def _cpi_via_worldbank() -> dict:
    """
    World Bank API for Taiwan CPI annual % change (fallback).
    Indicator FP.CPI.TOTL.ZG = CPI inflation, annual %.
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
# MOPS 公開資訊觀測站 — 寶島眼鏡 (2107)
# ─────────────────────────────────────────────

def fetch_mops_baodao() -> dict:
    result = {
        "period": None,
        "revenue_100m": None,
        "net_income_100m": None,
        "error": None,
    }

    try:
        url = "https://mops.twse.com.tw/mops/web/ajax_t05st09_1"
        payload = {
            "encodeURIComponent": "1",
            "step": "1",
            "firstin": "1",
            "off": "1",
            "co_id": "2107",
            "TYPEK": "sii",
        }
        resp = requests.post(url, data=payload, headers=HEADERS, timeout=30)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) >= 2 and re.search(r"\d{3}[年/]\d{1,2}月", cells[0]):
                    try:
                        revenue_k = float(cells[1].replace(",", ""))
                        result["period"] = cells[0]
                        result["revenue_100m"] = round(revenue_k / 100_000, 2)
                    except (ValueError, IndexError):
                        pass
                    break

        if result["period"]:
            _enrich_quarterly(result)

    except Exception as exc:
        logger.warning("MOPS fetch failed: %s", exc)
        result["error"] = f"寶島眼鏡財報暫時無法取得: {exc}"

    return result


def _enrich_quarterly(result: dict) -> None:
    try:
        now = datetime.now()
        roc_year = now.year - 1911
        quarter = (now.month - 1) // 3
        if quarter == 0:
            quarter = 4
            roc_year -= 1

        url = "https://mops.twse.com.tw/mops/web/ajax_t163sb04"
        payload = {
            "encodeURIComponent": "1",
            "step": "1",
            "firstin": "1",
            "off": "1",
            "co_id": "2107",
            "year": str(roc_year),
            "season": str(quarter).zfill(2),
            "TYPEK": "sii",
        }
        resp = requests.post(url, data=payload, headers=HEADERS, timeout=30)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                text = " ".join(cells)
                if "稅後淨利" in text or "本期淨利" in text:
                    for cell in cells:
                        try:
                            val = float(cell.replace(",", ""))
                            result["net_income_100m"] = round(val / 100_000, 2)
                            result["period"] = f"{roc_year + 1911}Q{quarter}"
                            break
                        except ValueError:
                            continue
    except Exception as exc:
        logger.debug("Quarterly enrichment failed: %s", exc)


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def fetch_all() -> dict:
    logger.info("Fetching MOEA retail data…")
    moea = fetch_moea_retail()

    logger.info("Fetching CPI data (OECD API)…")
    cpi = fetch_cpi()

    logger.info("Fetching MOPS 寶島眼鏡 data…")
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
