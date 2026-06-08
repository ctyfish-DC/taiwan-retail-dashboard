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
    """Scrape MOEA statistics hub page for Excel download link."""
    hub_url = "https://www.moea.gov.tw/Mns/dos/content/wHandMenuFile.ashx?mid=9861"
    resp = requests.get(hub_url, headers=HEADERS, timeout=30)
    logger.warning("MOEA hub HTTP %d, content-length=%d", resp.status_code, len(resp.content))
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    links = soup.find_all("a", href=True)
    logger.warning("MOEA hub: found %d links", len(links))
    for a in links:
        href = a["href"]
        if not href.startswith("http"):
            href = "https://www.moea.gov.tw" + href
        if href.endswith((".xlsx", ".xls")):
            r = requests.get(href, headers=HEADERS, timeout=60)
            r.raise_for_status()
            df = pd.read_excel(io.BytesIO(r.content), header=None)
            if df is not None and "零售" in df.to_string():
                return df
        elif href.endswith(".csv"):
            r = requests.get(href, headers=HEADERS, timeout=60)
            r.raise_for_status()
            df = _read_csv_bytes(r.content)
            if df is not None and not df.empty:
                return df
    raise ValueError(f"No Excel/CSV links found on MOEA hub (found {len(links)} total links)")


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

    # values = {"2023": 2.49, "2024": 2.16, ...}
    latest_year = max(values.keys(), key=lambda y: int(y))
    yoy = round(float(values[latest_year]), 2)

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
        # Try both 上市 (sii) and 上櫃 (otc)
        for typek in ("otc", "sii"):
            payload = {
                "encodeURIComponent": "1",
                "step": "1",
                "firstin": "1",
                "off": "1",
                "co_id": "2107",
                "TYPEK": typek,
            }
            resp = requests.post(url, data=payload, headers=HEADERS, timeout=30)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            tables = soup.find_all("table")
            logger.warning("MOPS typek=%s: HTTP %d, tables=%d", typek, resp.status_code, len(tables))

            for ti, table in enumerate(tables):
                rows = table.find_all("tr")
                for row in rows[:5]:  # Log first 5 rows for debugging
                    cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                    if cells:
                        logger.warning("MOPS table[%d] row sample: %s", ti, cells[:4])
                        break

            for table in tables:
                for row in table.find_all("tr"):
                    cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                    if not cells:
                        continue
                    # Match ROC date patterns: "115年1月", "115/01", "11501"
                    date_match = (
                        re.search(r"\d{3}[年/]\d{1,2}", cells[0])
                        or (len(cells[0]) == 5 and cells[0].isdigit())
                    )
                    if len(cells) >= 2 and date_match:
                        try:
                            revenue_k = float(cells[1].replace(",", ""))
                            result["period"] = cells[0]
                            result["revenue_100m"] = round(revenue_k / 100_000, 2)
                        except (ValueError, IndexError):
                            pass
                        break
            if result["period"]:
                logger.info("MOPS fetched with TYPEK=%s, period=%s", typek, result["period"])
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
