"""
fetch_data.py
Fetches Taiwan retail statistics from:
  - MOEA 經濟部統計處: Overall retail monthly revenue (YoY/MoM) and 眼鏡行 category
  - DGBAS 主計總處: CPI data
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
    )
}

# ─────────────────────────────────────────────
# MOEA 經濟部統計處 — Retail Sales
# ─────────────────────────────────────────────

def fetch_moea_retail() -> dict:
    result = {"overall": None, "eyewear": None, "error": None}

    # Try multiple approaches in order
    for attempt in (_moea_via_open_data, _moea_via_hub_scrape, _moea_via_direct_query):
        try:
            df = attempt()
            if df is not None and not df.empty:
                result["overall"] = _parse_moea_overall(df)
                result["eyewear"] = _parse_moea_eyewear(df)
                if result["overall"] is not None:
                    return result
        except Exception as exc:
            logger.debug("MOEA attempt %s failed: %s", attempt.__name__, exc)

    result["error"] = "MOEA 資料暫時無法取得（資料來源維護中）"
    logger.warning("All MOEA fetch attempts failed")
    return result


def _moea_via_open_data() -> Optional[pd.DataFrame]:
    """Try data.gov.tw open data search for the retail dataset."""
    # Search by keyword instead of hardcoded dataset ID
    search_url = "https://data.gov.tw/api/v2/datasets?keyword=零售業營業額&limit=5&_format=json"
    resp = requests.get(search_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    datasets = resp.json().get("result", {}).get("results", [])
    for ds in datasets:
        for res in ds.get("resources", []):
            url = res.get("download_url", "")
            if url.lower().endswith(".csv"):
                return _download_csv(url)
            if url.lower().endswith((".xlsx", ".xls")):
                r = requests.get(url, headers=HEADERS, timeout=60)
                r.raise_for_status()
                return pd.read_excel(io.BytesIO(r.content), header=None)
    raise ValueError("No CSV/Excel found in open data search")


def _moea_via_hub_scrape() -> Optional[pd.DataFrame]:
    """Scrape MOEA statistics hub page for Excel download link."""
    urls_to_try = [
        "https://www.moea.gov.tw/Mns/dos/content/wHandMenuFile.ashx?mid=9861",
        "https://www.moea.gov.tw/Mns/dos/content/Content.aspx?menu_id=9861",
    ]
    for hub_url in urls_to_try:
        try:
            resp = requests.get(hub_url, headers=HEADERS, timeout=30)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.endswith((".xlsx", ".xls", ".csv")):
                    if not href.startswith("http"):
                        href = "https://www.moea.gov.tw" + href
                    file_resp = requests.get(href, headers=HEADERS, timeout=60)
                    file_resp.raise_for_status()
                    if href.endswith(".csv"):
                        return _download_csv(href)
                    return pd.read_excel(io.BytesIO(file_resp.content), header=None)
        except Exception as exc:
            logger.debug("Hub scrape failed for %s: %s", hub_url, exc)
    raise ValueError("No Excel/CSV found on MOEA hub pages")


def _moea_via_direct_query() -> Optional[pd.DataFrame]:
    """POST query to MOEA investigation interface."""
    url = "https://dmz26.moea.gov.tw/GMWeb/investigate/InvestigateDA.aspx"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    # Look for any table with 零售 data
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        text = " ".join(r.get_text() for r in rows)
        if "零售" in text and len(rows) > 3:
            dfs = pd.read_html(str(table))
            if dfs:
                return dfs[0]
    raise ValueError("No retail table found on MOEA query page")


def _download_csv(url: str) -> Optional[pd.DataFrame]:
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    for enc in ("utf-8-sig", "big5", "cp950"):
        try:
            return pd.read_csv(io.StringIO(resp.content.decode(enc)))
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    return None


def _parse_moea_overall(df: pd.DataFrame) -> Optional[dict]:
    try:
        df.columns = [str(c).strip() for c in df.columns]
        mask = df.apply(lambda col: col.astype(str).str.contains("零售業$", regex=True)).any(axis=1)
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


def _parse_moea_eyewear(df: pd.DataFrame) -> Optional[dict]:
    try:
        mask = df.apply(lambda col: col.astype(str).str.contains("眼鏡")).any(axis=1)
        row = df[mask]
        if row.empty:
            return None
        row = row.iloc[0]
        values = pd.to_numeric(row, errors="coerce").dropna()
        if len(values) < 1:
            return None
        rev_latest = float(values.iloc[-1])
        return {
            "month": _guess_latest_month(df),
            "revenue_100m": round(rev_latest / 100, 1),
            "yoy_pct": None,
        }
    except Exception as exc:
        logger.debug("_parse_moea_eyewear failed: %s", exc)
        return None


def _guess_latest_month(df: pd.DataFrame) -> str:
    for cell in df.values.flatten():
        s = str(cell)
        m = re.search(r"(\d{3})(\d{2})", s)
        if m:
            roc_year = int(m.group(1))
            month = int(m.group(2))
            if 1 <= month <= 12:
                return f"{roc_year + 1911}年{month}月"
        m = re.search(r"(\d{4})[年/](\d{1,2})月?", s)
        if m:
            return f"{m.group(1)}年{int(m.group(2))}月"
    now = datetime.now()
    # Report is published ~2 months after period end
    month = now.month - 2 if now.month > 2 else now.month + 10
    year = now.year if now.month > 2 else now.year - 1
    return f"{year}年{month}月"


# ─────────────────────────────────────────────
# DGBAS 主計總處 — CPI
# ─────────────────────────────────────────────

def fetch_cpi() -> dict:
    result = {"month": None, "cpi": None, "yoy_pct": None, "error": None}

    for attempt in (_cpi_via_dgbas_csv, _cpi_via_stat_page, _cpi_via_open_data):
        try:
            parsed = attempt()
            if parsed.get("cpi") is not None:
                result.update(parsed)
                return result
        except Exception as exc:
            logger.debug("CPI attempt %s failed: %s", attempt.__name__, exc)

    result["error"] = "CPI 資料暫時無法取得（資料來源維護中）"
    logger.warning("All CPI fetch attempts failed")
    return result


def _cpi_via_dgbas_csv() -> dict:
    """Try known DGBAS CSV file paths."""
    candidate_urls = [
        "https://www.dgbas.gov.tw/public/Data/dgbas03/bs3/price/cpimainitem.csv",
        "https://ws.dgbas.gov.tw/001/Upload/463/relfile/10415/2181/cpisub.csv",
        "https://www.stat.gov.tw/public/Data/cpisub.csv",
    ]
    for url in candidate_urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            for enc in ("utf-8-sig", "big5", "cp950", "utf-8"):
                try:
                    df = pd.read_csv(io.StringIO(resp.content.decode(enc)))
                    parsed = _parse_cpi_df(df)
                    if parsed.get("cpi") is not None:
                        return parsed
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("DGBAS CSV %s failed: %s", url, exc)
    raise ValueError("No working DGBAS CSV URL found")


def _cpi_via_stat_page() -> dict:
    """Scrape 主計總處 CPI press release page."""
    urls = [
        "https://www.stat.gov.tw/News_Content.aspx?n=2672&s=66461",
        "https://www.dgbas.gov.tw/ct.asp?xItem=15150&ctNode=3249",
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")

            # Try to find CPI value in any table
            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                for row in rows:
                    cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                    text = " ".join(cells)
                    if ("總指數" in text or "綜合" in text) and len(cells) >= 2:
                        # Last numeric-looking cell is the CPI
                        for cell in reversed(cells):
                            try:
                                val = float(cell.replace(",", ""))
                                if 80 < val < 200:  # sanity check for CPI range
                                    return {
                                        "month": _guess_current_month(),
                                        "cpi": round(val, 2),
                                        "yoy_pct": None,
                                    }
                            except ValueError:
                                continue

            # Also try finding numbers inline
            text = soup.get_text()
            m = re.search(r"總指數[^\d]*(\d{2,3}\.\d{1,2})", text)
            if m:
                return {
                    "month": _guess_current_month(),
                    "cpi": float(m.group(1)),
                    "yoy_pct": None,
                }
        except Exception as exc:
            logger.debug("Stat page %s failed: %s", url, exc)
    raise ValueError("Could not parse CPI from any stat page")


def _cpi_via_open_data() -> dict:
    """Try data.gov.tw keyword search for CPI."""
    search_url = "https://data.gov.tw/api/v2/datasets?keyword=消費者物價指數&limit=5&_format=json"
    resp = requests.get(search_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    datasets = resp.json().get("result", {}).get("results", [])
    for ds in datasets:
        for res in ds.get("resources", []):
            url = res.get("download_url", "")
            if url.lower().endswith(".csv"):
                r = requests.get(url, headers=HEADERS, timeout=60)
                r.raise_for_status()
                for enc in ("utf-8-sig", "big5", "cp950"):
                    try:
                        df = pd.read_csv(io.StringIO(r.content.decode(enc)))
                        parsed = _parse_cpi_df(df)
                        if parsed.get("cpi") is not None:
                            return parsed
                    except Exception:
                        continue
    raise ValueError("No CPI CSV found via open data search")


def _parse_cpi_df(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        raise ValueError("Empty CPI dataframe")
    df.columns = [str(c).strip() for c in df.columns]
    # Find CPI total index column
    cpi_col = None
    for col in df.columns:
        if any(kw in col for kw in ("總指數", "CPI", "綜合", "General")):
            cpi_col = col
            break
    if cpi_col is None:
        numeric_cols = df.select_dtypes(include="number").columns
        if len(numeric_cols) == 0:
            raise ValueError("No numeric columns in CPI dataframe")
        cpi_col = numeric_cols[-1]

    df = df.dropna(subset=[cpi_col])
    latest = df.iloc[-1]
    prev_year = df.iloc[-13] if len(df) >= 13 else None

    cpi_val = float(latest[cpi_col])
    if not (80 < cpi_val < 200):
        raise ValueError(f"CPI value {cpi_val} out of expected range")

    month_label = str(latest[df.columns[0]])
    m = re.search(r"(\d{3,4})[年/](\d{1,2})", month_label)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if y < 200:
            y += 1911
        month_label = f"{y}年{mo}月"

    yoy = None
    if prev_year is not None:
        prev_cpi = float(prev_year[cpi_col])
        if prev_cpi:
            yoy = round((cpi_val - prev_cpi) / prev_cpi * 100, 2)

    return {"month": month_label, "cpi": round(cpi_val, 2), "yoy_pct": yoy}


def _guess_current_month() -> str:
    now = datetime.now()
    # CPI is typically available for previous month
    month = now.month - 1 if now.month > 1 else 12
    year = now.year if now.month > 1 else now.year - 1
    return f"{year}年{month}月"


# ─────────────────────────────────────────────
# MOPS 公開資訊觀測站 — 寶島眼鏡 (2107)
# ─────────────────────────────────────────────

def fetch_mops_baodao() -> dict:
    result = {
        "period": None,
        "revenue_100m": None,
        "gross_profit_100m": None,
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
            rows = table.find_all("tr")
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) >= 2 and re.search(r"\d{3}[年/]\d{1,2}月", cells[0]):
                    period = cells[0]
                    try:
                        revenue_k = float(cells[1].replace(",", ""))
                        result["period"] = period
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
        url = "https://mops.twse.com.tw/mops/web/ajax_t163sb04"
        now = datetime.now()
        roc_year = now.year - 1911
        quarter = (now.month - 1) // 3
        if quarter == 0:
            quarter = 4
            roc_year -= 1
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
                if "營業毛利" in text:
                    for cell in cells:
                        try:
                            val = float(cell.replace(",", ""))
                            result["gross_profit_100m"] = round(val / 100_000, 2)
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

    logger.info("Fetching CPI data…")
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
