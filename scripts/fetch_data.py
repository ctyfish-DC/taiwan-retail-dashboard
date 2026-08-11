"""
fetch_data.py
Fetches Taiwan retail stock monthly revenue:
  - 個股月營收（寶島光學 5312、寶利徠 1813）: MOPS 月營收（經 FinMind 鏡像，境外可用）
    股價/市值/最近一季獲利率取自 Yahoo Finance

台灣上市/上櫃公司依規定須於次月 10 日前公告月營收，FinMind 每日同步 MOPS 資料，
因此每月抓取皆可拿到最新一個月的數字。

注意：台灣法規僅強制公告「月營收」，毛利率／稅後淨利等損益數字依規定只在季報／
半年報／年報揭露（無官方月度損益資料），因此本程式的獲利率/淨利數字一律標示
為「最近一季」，與月營收區分開來，避免誤讀成月度數字。
"""

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 個股月營收 + 股價 — FinMind（MOPS 鏡像）+ yfinance
# ─────────────────────────────────────────────

# 追蹤的公司清單：.TWO = 上櫃、.TW = 上市（Yahoo Finance 代號後綴）
COMPANIES = [
    {"co_id": "5312", "name": "寶島光學科技", "ticker": "5312.TWO"},
    {"co_id": "1813", "name": "寶利徠光學科技", "ticker": "1813.TW"},
]

RECENT_MONTHS_N = 6  # 近幾個月的月營收趨勢


def fetch_stock(name: str, yahoo_ticker: str) -> dict:
    result = {
        "name": name,
        "co_id": yahoo_ticker.split(".")[0],
        "revenue_source": None,
        # 主要指標：最新單月營收（優先來源，單位：萬元）
        "latest_month_label": None,
        "latest_month_revenue_10k": None,
        "latest_month_mom_pct": None,
        "latest_month_yoy_pct": None,
        "recent_months": [],       # 近幾個月趨勢（舊→新，含最新月）: {label, revenue_10k}
        "narrative": None,         # 營收趨勢文字摘要（月增減 + 連續年增/減月數）
        # 備援指標：Yahoo 季報，FinMind 抓不到月資料時使用（單位：萬元）
        "quarters": [],
        # 今年累計（單位：萬元）
        "ytd_label": None,
        "ytd_revenue_10k": None,
        "ytd_yoy_pct": None,
        # 最近一季獲利率（Yahoo Finance；台灣法規僅季報揭露損益，無月度資料）
        "gross_margin_pct": None,
        "net_income_100m": None,
        "close_price": None,
        "price_change_pct": None,
        "market_cap_100m": None,
        "error": None,
    }

    # ── 股價 / 估值 / 最近一季獲利率：yfinance ──
    ticker = None
    try:
        import yfinance as yf
        ticker = yf.Ticker(yahoo_ticker)
        info = ticker.info

        # 上市/上櫃後綴猜錯時換另一個再試
        if not (info.get("currentPrice") or info.get("regularMarketPrice")):
            base, suffix = yahoo_ticker.split(".")
            alt = f"{base}.{'TW' if suffix == 'TWO' else 'TWO'}"
            logger.warning("%s: no price for %s, retrying %s", name, yahoo_ticker, alt)
            ticker = yf.Ticker(alt)
            info = ticker.info

        result["close_price"] = info.get("currentPrice") or info.get("regularMarketPrice")

        market_cap = info.get("marketCap")
        if market_cap:
            result["market_cap_100m"] = round(market_cap / 100_000_000, 1)

        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        if result["close_price"] and prev_close:
            result["price_change_pct"] = round(
                (result["close_price"] - prev_close) / prev_close * 100, 2
            )

        gross_margins = info.get("grossMargins")
        if gross_margins is not None:
            result["gross_margin_pct"] = round(gross_margins * 100, 1)

        net_income = info.get("netIncomeToCommon")
        if net_income:
            result["net_income_100m"] = round(net_income / 100_000_000, 2)

        if result["close_price"]:
            logger.info("%s yfinance OK: price=%s", name, result["close_price"])
        else:
            logger.warning("%s: price not found in yfinance info", name)

    except Exception as exc:
        logger.warning("%s yfinance failed: %s", name, exc)
        result["error"] = str(exc)

    # ── 營收：優先 FinMind（MOPS 月營收鏡像，最新），失敗才退回 Yahoo 季報 ──
    try:
        months = _monthly_revenue_finmind(result["co_id"])
        latest, recent, ytd = _monthly_summary(months, RECENT_MONTHS_N)
        if latest:
            result["revenue_source"] = "MOPS月營收(FinMind)"
            result["latest_month_label"] = latest["label"]
            result["latest_month_revenue_10k"] = latest["revenue_10k"]
            result["latest_month_mom_pct"] = latest["mom_pct"]
            result["latest_month_yoy_pct"] = latest["yoy_pct"]
            result["recent_months"] = recent
            result["narrative"] = _revenue_narrative(months, latest)
        if ytd:
            result["ytd_label"] = ytd["label"]
            result["ytd_revenue_10k"] = ytd["revenue_10k"]
            result["ytd_yoy_pct"] = ytd["yoy_pct"]
        logger.info(
            "%s FinMind revenue OK: latest=%s, %d recent months, ytd=%s",
            name, latest and latest["label"], len(recent), ytd and ytd["label"],
        )
    except Exception as exc:
        logger.warning("%s FinMind failed (%s), falling back to Yahoo quarterly", name, exc)
        if ticker is not None:
            _revenue_from_yfinance(ticker, result)

    return result


FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _monthly_revenue_finmind(co_id: str) -> dict:
    """月營收 {(year, month): revenue_元}。來源為 MOPS 公開資訊觀測站，經 FinMind 鏡像
    （台灣政府網站封鎖境外 IP，FinMind 的 API 全球可用）。"""
    start = f"{datetime.now().year - 2}-01-01"
    resp = requests.get(
        FINMIND_URL,
        params={
            "dataset": "TaiwanStockMonthRevenue",
            "data_id": co_id,
            "start_date": start,
        },
        timeout=25,
    )
    resp.raise_for_status()
    body = resp.json()
    rows = body.get("data") or []
    months = {}
    for r in rows:
        y, m, v = r.get("revenue_year"), r.get("revenue_month"), r.get("revenue")
        if y and m and v:
            months[(int(y), int(m))] = float(v)
    if not months:
        raise ValueError(f"no monthly revenue rows (msg={body.get('msg')})")

    # 單位防呆：MOPS 原始為千元、FinMind 通常已轉為元；數值過小視為千元
    vals = sorted(months.values())
    median = vals[len(vals) // 2]
    if median < 1e7:
        months = {k: v * 1000 for k, v in months.items()}
        logger.info("FinMind %s: values look like 千元, scaled x1000", co_id)
    return months


def _monthly_summary(months: dict, recent_n: int = 6):
    """由 {(year,month): revenue_元} 組出（金額單位一律換算為萬元）：
    - 最新單月營收（含 MoM% 與去年同月 YoY%）
    - 近 recent_n 個月趨勢（舊→新，含最新月）
    - 今年累計營收（含去年同期 YoY%）
    「最新月」一律取資料裡實際存在的最大 (year, month)，不假設等於執行當下的月份，
    如遇公司延後公告，會自然顯示上一個可取得的月份，不會出錯。
    """
    if not months:
        return None, [], None

    sorted_keys = sorted(months.keys())
    latest_y, latest_m = sorted_keys[-1]
    latest_val = months[(latest_y, latest_m)]

    prev_m_key = (latest_y, latest_m - 1) if latest_m > 1 else (latest_y - 1, 12)
    prev_m_val = months.get(prev_m_key)
    mom_pct = round((latest_val - prev_m_val) / prev_m_val * 100, 1) if prev_m_val else None

    yoy_val = months.get((latest_y - 1, latest_m))
    yoy_pct = round((latest_val - yoy_val) / yoy_val * 100, 1) if yoy_val else None

    latest = {
        "label": f"{latest_y}年{latest_m}月",
        "revenue_10k": round(latest_val / 1e4, 1),
        "mom_pct": mom_pct,
        "yoy_pct": yoy_pct,
    }

    recent = [
        {"label": f"{y}/{m}", "revenue_10k": round(months[(y, m)] / 1e4, 1)}
        for y, m in sorted_keys[-recent_n:]
    ]

    cur_months = sorted(m for (y, m) in months if y == latest_y)
    cur_sum = sum(months[(latest_y, m)] for m in cur_months)
    ytd = {
        "label": f"{latest_y}/1–{max(cur_months)}月",
        "revenue_10k": round(cur_sum / 1e4, 1),
        "yoy_pct": None,
    }
    if all((latest_y - 1, m) in months for m in cur_months):
        prev_sum = sum(months[(latest_y - 1, m)] for m in cur_months)
        if prev_sum:
            ytd["yoy_pct"] = round((cur_sum - prev_sum) / prev_sum * 100, 1)

    return latest, recent, ytd


def _yoy_streak(months: dict, latest_y: int, latest_m: int):
    """由最新月往回逐月檢查 YoY 正負號，回傳 (方向, 連續月數)。
    方向為「成長」或「衰退」；遇到資料缺漏或正負號翻轉就停止。
    """
    y, m = latest_y, latest_m
    direction = None
    streak = 0
    while True:
        cur = months.get((y, m))
        prev = months.get((y - 1, m))
        if not cur or not prev:
            break
        pct = (cur - prev) / prev
        if abs(pct) < 0.005:  # 視為持平，不計入連續趨勢
            break
        sign = "成長" if pct > 0 else "衰退"
        if direction is None:
            direction = sign
        elif sign != direction:
            break
        streak += 1
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return direction, streak


def _revenue_narrative(months: dict, latest: dict) -> str:
    """用月營收原始數據組一句話摘要（月增減 + 是否連續數月年增/年減）。
    這段完全從官方月營收數字（MOPS，經 FinMind）計算得出，不涉及任何損益資訊。
    """
    label = latest["label"]
    y, m = int(label.split("年")[0]), int(label.split("年")[1].rstrip("月"))
    direction, streak = _yoy_streak(months, y, m)

    bits = []
    mom = latest.get("mom_pct")
    if mom is not None:
        if mom > 0.5:
            bits.append(f"月增{mom:.1f}%")
        elif mom < -0.5:
            bits.append(f"月減{abs(mom):.1f}%")
        else:
            bits.append("與上月持平")

    if direction and streak >= 2:
        verb = "年增" if direction == "成長" else "年減"
        bits.append(f"已連續{streak}個月{verb}")
    else:
        yoy = latest.get("yoy_pct")
        if yoy is not None:
            if yoy > 0.5:
                bits.append(f"年增{yoy:.1f}%")
            elif yoy < -0.5:
                bits.append(f"年減{abs(yoy):.1f}%")
            else:
                bits.append("與去年同月持平")

    return "、".join(bits)


def _revenue_from_yfinance(ticker, result: dict) -> None:
    """備援：Yahoo 季報（無法拆到單月）。台股中小型股更新較慢，資料可能落後。"""
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
            return

        revenue_row = revenue_row.dropna().sort_index()  # 舊→新
        items = [(d, v) for d, v in revenue_row.items()]
        if not items:
            return

        rev_by_q = {(d.year, (d.month + 2) // 3): v for d, v in items}
        quarters = []
        for (y, q), v in sorted(rev_by_q.items())[-4:]:
            prev = rev_by_q.get((y - 1, q))
            quarters.append({
                "label": f"{y} Q{q}",
                "revenue_10k": round(v / 1e4, 1),
                "yoy_pct": round((v - prev) / prev * 100, 1) if prev else None,
            })
        result["quarters"] = quarters
        result["revenue_source"] = "Yahoo季報(可能落後，無單月資料)"

        latest_year = max(y for y, _ in rev_by_q)
        year_qs = [(q, v) for (y, q), v in rev_by_q.items() if y == latest_year]
        year_total = sum(v for _, v in year_qs)
        result["ytd_label"] = f"{latest_year} Q1–Q{len(year_qs)}"
        result["ytd_revenue_10k"] = round(year_total / 1e4, 1)
    except Exception as exc:
        logger.warning("yfinance revenue fallback failed: %s", exc)


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def fetch_all() -> dict:
    stocks = []
    for c in COMPANIES:
        logger.info("Fetching %s (%s)…", c["name"], c["co_id"])
        stocks.append(fetch_stock(c["name"], c["ticker"]))

    return {
        "stocks": stocks,
        "fetched_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
    }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(fetch_all(), ensure_ascii=False, indent=2))
