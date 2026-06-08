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
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ─────────────────────────────────────────────
# MOEA 經濟部統計處 — Retail Sales
# ─────────────────────────────────────────────

def fetch_moea_retail() -> dict:
    """
    Fetch overall retail and 眼鏡行 monthly revenue from MOEA open data.

    Returns a dict with keys:
        overall: {month, revenue_100m, yoy_pct, mom_pct}
        eyewear: {month, revenue_100m, yoy_pct}
        error: str | None
    """
    result = {
        "overall": None,
        "eyewear": None,
        "error": None,
    }

    # The 批發、零售及餐飲業 statistics page lists downloadable Excel files.
    # We try the government open-data CSV endpoint first, then fall back to
    # scraping the MOEA statistics hub for the latest Excel/CSV link.
    try:
        # ── Approach 1: data.gov.tw open data (dataset 6889) ──────────────
        # The dataset provides a JSON resource list; we grab the CSV download URL.
        api_url = (
            "https://data.gov.tw/api/v2/datasets/6889"
            "?format=json&_format=json"
        )
        resp = requests.get(api_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        resources = resp.json().get("result", {}).get("resources", [])
        csv_url = None
        for r in resources:
            url = r.get("download_url", "")
            if url.lower().endswith(".csv"):
                csv_url = url
                break

        if csv_url:
            df = _download_moea_csv(csv_url)
        else:
            df = _scrape_moea_excel()

        if df is not None and not df.empty:
            result["overall"] = _parse_moea_overall(df)
            result["eyewear"] = _parse_moea_eyewear(df)
        else:
            raise ValueError("MOEA dataframe is empty after download")

    except Exception as exc:
        logger.warning("MOEA retail fetch failed: %s", exc)
        # ── Fallback: direct scrape of statistics hub page ────────────────
        try:
            df = _scrape_moea_excel()
            if df is not None and not df.empty:
                result["overall"] = _parse_moea_overall(df)
                result["eyewear"] = _parse_moea_eyewear(df)
            else:
                result["error"] = f"MOEA data unavailable: {exc}"
        except Exception as exc2:
            result["error"] = f"MOEA data unavailable: {exc2}"

    return result


def _download_moea_csv(url: str) -> Optional[pd.DataFrame]:
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    # Try UTF-8-SIG (BOM) first, then big5
    for enc in ("utf-8-sig", "big5", "cp950"):
        try:
            return pd.read_csv(io.StringIO(resp.content.decode(enc)))
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    return None


def _scrape_moea_excel() -> Optional[pd.DataFrame]:
    """
    Scrape the MOEA statistics page to find the latest retail Excel/CSV file,
    then download and return as DataFrame.
    """
    hub_url = (
        "https://www.moea.gov.tw/Mns/dos/content/wHandMenuFile.ashx?mid=9861"
    )
    resp = requests.get(hub_url, headers=HEADERS, timeout=30)
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    # Look for links containing 零售 or .xlsx/.csv
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text()
        if ("零售" in text or "retail" in text.lower()) and (
            href.endswith(".xlsx") or href.endswith(".xls") or href.endswith(".csv")
        ):
            if not href.startswith("http"):
                href = "https://www.moea.gov.tw" + href
            file_resp = requests.get(href, headers=HEADERS, timeout=60)
            file_resp.raise_for_status()
            if href.endswith(".csv"):
                return _download_moea_csv(href)
            else:
                return pd.read_excel(io.BytesIO(file_resp.content), header=None)

    # Generic fallback: grab first xlsx/xls on the page
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.endswith(".xlsx") or href.endswith(".xls"):
            if not href.startswith("http"):
                href = "https://www.moea.gov.tw" + href
            file_resp = requests.get(href, headers=HEADERS, timeout=60)
            file_resp.raise_for_status()
            return pd.read_excel(io.BytesIO(file_resp.content), header=None)

    return None


def _parse_moea_overall(df: pd.DataFrame) -> Optional[dict]:
    """
    Attempt to extract overall retail figures from a raw MOEA DataFrame.
    Column names vary by year; we use heuristic matching.
    """
    try:
        # Normalise column names
        df.columns = [str(c).strip() for c in df.columns]

        # Try to find a row labelled '零售業' (retail industry total)
        mask = df.apply(lambda col: col.astype(str).str.contains("零售業$", regex=True)).any(axis=1)
        row = df[mask]
        if row.empty:
            # Fallback: first data row after header
            row = df.iloc[[2]]

        row = row.iloc[0]
        values = pd.to_numeric(row, errors="coerce").dropna()

        if len(values) < 2:
            return None

        # Last two numeric values are assumed to be (previous month, latest month)
        rev_latest = float(values.iloc[-1])
        rev_prev = float(values.iloc[-2])

        # Try to get YoY from the DataFrame if available, else mark N/A
        yoy = None
        mom = None
        if len(values) >= 3:
            # Some formats include YoY as the second-to-last-but-one column
            mom = round((rev_latest - rev_prev) / rev_prev * 100, 1) if rev_prev else None

        # Determine month label from DataFrame or use current
        month_label = _guess_latest_month(df)

        return {
            "month": month_label,
            "revenue_100m": round(rev_latest / 100, 1),  # values in 百萬, convert to 億
            "yoy_pct": yoy,
            "mom_pct": mom,
        }
    except Exception as exc:
        logger.warning("_parse_moea_overall failed: %s", exc)
        return None


def _parse_moea_eyewear(df: pd.DataFrame) -> Optional[dict]:
    """Extract 眼鏡行 category row."""
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
        rev_prev = float(values.iloc[-2]) if len(values) >= 2 else None
        yoy = None
        mom_label = _guess_latest_month(df)
        return {
            "month": mom_label,
            "revenue_100m": round(rev_latest / 100, 1),
            "yoy_pct": yoy,
        }
    except Exception as exc:
        logger.warning("_parse_moea_eyewear failed: %s", exc)
        return None


def _guess_latest_month(df: pd.DataFrame) -> str:
    """Try to read a year/month header from df, else return current YM."""
    for cell in df.values.flatten():
        s = str(cell)
        # Match patterns like 11301, 113年1月, 2024/01, etc.
        m = re.search(r"(\d{3})(\d{2})", s)
        if m:
            roc_year = int(m.group(1))
            month = int(m.group(2))
            ce_year = roc_year + 1911
            return f"{ce_year}年{month}月"
        m = re.search(r"(\d{4})[年/](\d{1,2})月?", s)
        if m:
            return f"{m.group(1)}年{int(m.group(2))}月"
    now = datetime.now()
    return f"{now.year}年{now.month}月"


# ─────────────────────────────────────────────
# DGBAS 主計總處 — CPI
# ─────────────────────────────────────────────

def fetch_cpi() -> dict:
    """
    Fetch the latest CPI data from 主計總處.

    Returns:
        {month, cpi, yoy_pct, error}
    """
    result = {"month": None, "cpi": None, "yoy_pct": None, "error": None}

    try:
        # 主計總處 open data API for CPI (總指數)
        # Dataset: https://www.stat.gov.tw/  /  https://ws.dgbas.gov.tw
        # We use the open data JSON from data.gov.tw for CPI (dataset 6717 / 6718)
        api_url = (
            "https://data.gov.tw/api/v2/datasets/6717"
            "?format=json&_format=json"
        )
        resp = requests.get(api_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        resources = resp.json().get("result", {}).get("resources", [])
        csv_url = None
        for r in resources:
            url = r.get("download_url", "")
            if url.lower().endswith(".csv"):
                csv_url = url
                break

        if csv_url:
            df = _download_moea_csv(csv_url)
            parsed = _parse_cpi_df(df)
            result.update(parsed)
        else:
            raise ValueError("No CSV found for CPI dataset")

    except Exception as exc:
        logger.warning("CPI data.gov.tw fetch failed: %s", exc)
        # Fallback: scrape 主計總處 CPI press release page
        try:
            result.update(_scrape_dgbas_cpi())
        except Exception as exc2:
            result["error"] = f"CPI data unavailable: {exc2}"

    return result


def _parse_cpi_df(df: pd.DataFrame) -> dict:
    """Parse a CPI DataFrame and return latest entry."""
    if df is None or df.empty:
        raise ValueError("Empty CPI dataframe")

    df.columns = [str(c).strip() for c in df.columns]

    # Look for column containing '總指數' or 'CPI'
    cpi_col = None
    for col in df.columns:
        if "總指數" in col or "CPI" in col.upper() or "綜合" in col:
            cpi_col = col
            break
    if cpi_col is None:
        # Use last numeric column
        numeric_cols = df.select_dtypes(include="number").columns
        if len(numeric_cols) == 0:
            raise ValueError("No numeric columns in CPI dataframe")
        cpi_col = numeric_cols[-1]

    # Find month column
    month_col = df.columns[0]

    df = df.dropna(subset=[cpi_col])
    latest = df.iloc[-1]
    prev_year = df.iloc[-13] if len(df) >= 13 else None

    cpi_val = float(latest[cpi_col])
    month_label = str(latest[month_col])

    yoy = None
    if prev_year is not None:
        prev_cpi = float(prev_year[cpi_col])
        yoy = round((cpi_val - prev_cpi) / prev_cpi * 100, 2)

    # Normalise month label
    m = re.search(r"(\d{3,4})[年/](\d{1,2})", month_label)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        if y < 200:
            y += 1911
        month_label = f"{y}年{mo}月"

    return {"month": month_label, "cpi": round(cpi_val, 2), "yoy_pct": yoy}


def _scrape_dgbas_cpi() -> dict:
    """Scrape 主計總處 CPI statistics page as fallback."""
    url = "https://www.stat.gov.tw/News_Content.aspx?n=2672&s=66461"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    # Find a table with CPI data
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cells) >= 2 and "總指數" in cells[0]:
                try:
                    cpi_val = float(cells[-1].replace(",", ""))
                    return {
                        "month": _guess_current_month(),
                        "cpi": cpi_val,
                        "yoy_pct": None,
                    }
                except ValueError:
                    continue

    raise ValueError("Could not parse CPI from DGBAS page")


def _guess_current_month() -> str:
    now = datetime.now()
    return f"{now.year}年{now.month}月"


# ─────────────────────────────────────────────
# MOPS 公開資訊觀測站 — 寶島眼鏡 (2107)
# ─────────────────────────────────────────────

def fetch_mops_baodao() -> dict:
    """
    Fetch the latest financial report for 寶島眼鏡 (stock code 2107)
    from 公開資訊觀測站 MOPS.

    Returns:
        {period, revenue_100m, gross_profit_100m, net_income_100m, error}
    """
    result = {
        "period": None,
        "revenue_100m": None,
        "gross_profit_100m": None,
        "net_income_100m": None,
        "error": None,
    }

    try:
        # MOPS monthly revenue report (t05st09_1)
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

        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            # Find header row and data rows
            for i, row in enumerate(rows):
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) >= 3 and re.search(r"\d{3}[年/]\d{1,2}月", cells[0]):
                    # cells[0] = period (e.g. 113年1月), cells[2] = cumulative revenue
                    period = cells[0]
                    try:
                        revenue_k = float(cells[1].replace(",", ""))  # in thousands
                        result["period"] = period
                        result["revenue_100m"] = round(revenue_k / 100_000, 2)
                    except (ValueError, IndexError):
                        pass
                    break

        # If we got monthly revenue, try quarterly financial statements
        _enrich_quarterly(result)

    except Exception as exc:
        logger.warning("MOPS fetch failed: %s", exc)
        result["error"] = f"寶島眼鏡財報暫時無法取得: {exc}"

    return result


def _enrich_quarterly(result: dict) -> None:
    """
    Try to fetch the latest quarterly income statement from MOPS
    and add gross profit / net income.
    """
    try:
        url = "https://mops.twse.com.tw/mops/web/ajax_t163sb04"
        # Determine latest available quarter
        now = datetime.now()
        # ROC year
        roc_year = now.year - 1911
        quarter = (now.month - 1) // 3  # 0-based; latest *published* quarter
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

        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if "營業收入" in " ".join(cells) or "收入合計" in " ".join(cells):
                    for cell in cells:
                        try:
                            val = float(cell.replace(",", ""))
                            result["revenue_100m"] = round(val / 100_000, 2)
                            result["period"] = f"{roc_year + 1911}Q{quarter}"
                            break
                        except ValueError:
                            continue
                if "稅後淨利" in " ".join(cells) or "本期淨利" in " ".join(cells):
                    for cell in cells:
                        try:
                            val = float(cell.replace(",", ""))
                            result["net_income_100m"] = round(val / 100_000, 2)
                            break
                        except ValueError:
                            continue
                if "營業毛利" in " ".join(cells):
                    for cell in cells:
                        try:
                            val = float(cell.replace(",", ""))
                            result["gross_profit_100m"] = round(val / 100_000, 2)
                            break
                        except ValueError:
                            continue

    except Exception as exc:
        logger.debug("Quarterly enrichment failed (non-critical): %s", exc)


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def fetch_all() -> dict:
    """
    Fetch all data sources and return a combined dict.
    Never raises — each source records its own error.
    """
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
