"""
fetch_taiex.py
Fetches 台灣加權指數 (^TWII) daily OHLC from Yahoo Finance and computes
common technical indicators: RSI(14), MACD(12,26,9), KD(9,3,3),
Bollinger Bands(20,2), and MA5/20/60 cross events.

All indicator formulas are implemented directly with pandas/numpy
(no TA-Lib / pandas-ta dependency) to avoid CI installation issues
(TA-Lib needs a compiled C library) and third-party maintenance risk
(pandas-ta's upstream project is heading toward being archived).

也提供「情境觀察」(scenarios)：條件式的「若跌破/站上某關鍵價位，技術面
意義為何」句子（例如跌破月線=短期轉弱訊號），純粹是規則生成的假設性
說明，不是價格預測，也不涉及任何 AI 或外部模型。
"""

import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

TAIEX_TICKER = "^TWII"


# ─────────────────────────────────────────────
# 指標計算（純 pandas/numpy，無第三方技術分析套件）
# ─────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI，採 Wilder's smoothing（等同 EMA alpha=1/period）。"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _kd(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9):
    """台股慣用 KD：RSV 取 n 日高低，K/D 以遞迴平滑（權重 2/3, 1/3），初始值 50。"""
    lowest_low = low.rolling(n, min_periods=n).min()
    highest_high = high.rolling(n, min_periods=n).max()
    denom = (highest_high - lowest_low).replace(0, pd.NA)
    rsv = (close - lowest_low) / denom * 100

    k = pd.Series(index=close.index, dtype=float)
    d = pd.Series(index=close.index, dtype=float)
    prev_k, prev_d = 50.0, 50.0
    for i in range(len(close)):
        r = rsv.iloc[i]
        if pd.isna(r):
            k.iloc[i] = float("nan")
            d.iloc[i] = float("nan")
            continue
        cur_k = prev_k * 2 / 3 + float(r) * 1 / 3
        cur_d = prev_d * 2 / 3 + cur_k * 1 / 3
        k.iloc[i] = cur_k
        d.iloc[i] = cur_d
        prev_k, prev_d = cur_k, cur_d
    return k, d


def _bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid, mid + num_std * std, mid - num_std * std


def _cross_event(a: pd.Series, b: pd.Series):
    """判斷 a、b 兩序列最新一筆是否剛發生黃金交叉(a 由下往上穿 b)或死亡交叉(反之)。"""
    if len(a) < 2:
        return None
    a_prev, b_prev, a_cur, b_cur = a.iloc[-2], b.iloc[-2], a.iloc[-1], b.iloc[-1]
    if pd.isna(a_prev) or pd.isna(b_prev) or pd.isna(a_cur) or pd.isna(b_cur):
        return None
    prev_diff = a_prev - b_prev
    cur_diff = a_cur - b_cur
    if prev_diff <= 0 < cur_diff:
        return "golden"
    if prev_diff >= 0 > cur_diff:
        return "death"
    return None


# ─────────────────────────────────────────────
# 規則式文字判讀（純數字門檻，不涉及任何外部服務）
# ─────────────────────────────────────────────

def _rsi_reading(val):
    if val is None or pd.isna(val):
        return None
    if val >= 70:
        return "超買"
    if val <= 30:
        return "超賣"
    return "中性"


def _kd_reading(k_val):
    if k_val is None or pd.isna(k_val):
        return None
    if k_val >= 80:
        return "超買"
    if k_val <= 20:
        return "超賣"
    return "中性"


def _boll_reading(close_val, upper, lower):
    if pd.isna(upper) or pd.isna(lower):
        return "資料不足"
    if close_val > upper:
        return "站上布林上緣"
    if close_val < lower:
        return "跌破布林下緣"
    return "位於通道內"


def _ma_alignment(ma5, ma20, ma60):
    if pd.isna(ma5) or pd.isna(ma20) or pd.isna(ma60):
        return "資料不足"
    if ma5 > ma20 > ma60:
        return "多頭排列"
    if ma5 < ma20 < ma60:
        return "空頭排列"
    return "糾結整理"


def _recent_high_low(high: pd.Series, low: pd.Series, n: int = 20):
    """近 n 個交易日的最高/最低價，不含最新一筆（今天），
    這樣「若突破/跌破」的情境句才是指向一個尚未觸及的價位。"""
    hist_high, hist_low = high.iloc[:-1], low.iloc[:-1]
    if hist_high.empty:
        return None, None
    return (
        round(float(hist_high.iloc[-n:].max()), 1),
        round(float(hist_low.iloc[-n:].min()), 1),
    )


def _build_narrative(ind: dict) -> str:
    """組一句規則式技術面摘要，完全由上面算出的數字/門檻決定，不呼叫任何 AI。"""
    bits = []

    if ind.get("rsi_reading"):
        bits.append(f"RSI {ind['rsi']:.0f}（{ind['rsi_reading']}）")

    if ind.get("macd_cross") == "golden":
        bits.append("MACD黃金交叉")
    elif ind.get("macd_cross") == "death":
        bits.append("MACD死亡交叉")
    elif ind.get("macd_hist") is not None:
        bits.append("MACD站上訊號線" if ind["macd_hist"] > 0 else "MACD跌破訊號線")

    if ind.get("kd_cross") == "golden":
        bits.append("KD黃金交叉")
    elif ind.get("kd_cross") == "death":
        bits.append("KD死亡交叉")
    if ind.get("kd_reading") and ind["kd_reading"] != "中性":
        bits.append(f"K值{ind['k']:.0f}（{ind['kd_reading']}）")

    if ind.get("boll_reading"):
        bits.append(ind["boll_reading"])

    if ind.get("ma5_20_cross") == "golden":
        bits.append("5日均線上穿20日均線")
    elif ind.get("ma5_20_cross") == "death":
        bits.append("5日均線下穿20日均線")

    if ind.get("ma_alignment") and ind["ma_alignment"] != "資料不足":
        bits.append(ind["ma_alignment"])

    return "、".join(bits)


def _build_scenarios(t: dict) -> list:
    """產生「若觸及某關鍵價位，技術面意義為何」的條件式情境句。

    這不是預測漲跌，而是列出：目前站在哪個關鍵價位的哪一側、
    以及若價格「跨過」該價位在技術分析上通常代表的意義。
    全部由已算出的均線/近期高低/指標數值與固定規則生成，不涉及任何預測模型。
    """
    close = t.get("close")
    scenarios = []
    if close is None:
        return scenarios

    ma20 = t.get("ma20")
    if ma20 is not None:
        if close >= ma20:
            scenarios.append(f"目前站上月線（MA20 {ma20:,.0f}），若拉回跌破，短期轉弱訊號浮現")
        else:
            scenarios.append(f"目前跌破月線（MA20 {ma20:,.0f}），若站回其上，短期轉強訊號浮現")

    ma60 = t.get("ma60")
    if ma60 is not None:
        if close >= ma60:
            scenarios.append(f"目前站上季線（MA60 {ma60:,.0f}），中期偏多；若跌破，中期轉弱訊號浮現")
        else:
            scenarios.append(f"目前跌破季線（MA60 {ma60:,.0f}），中期偏弱；若站回其上，中期轉強訊號浮現")

    recent_high, recent_low = t.get("recent_high_20d"), t.get("recent_low_20d")
    if recent_high is not None and recent_low is not None:
        scenarios.append(
            f"近20日區間 {recent_low:,.0f}～{recent_high:,.0f}點："
            f"若突破 {recent_high:,.0f} 多方氣勢可能延續，若跌破 {recent_low:,.0f} 賣壓可能加重"
        )

    boll_upper, boll_lower = t.get("boll_upper"), t.get("boll_lower")
    if boll_upper is not None and boll_lower is not None:
        scenarios.append(
            f"布林通道 {boll_lower:,.0f}～{boll_upper:,.0f}點："
            f"站上上緣易現過熱拉回，跌破下緣則可能出現超跌反彈"
        )

    rsi = t.get("rsi")
    if rsi is not None:
        if 65 <= rsi < 70:
            scenarios.append(f"RSI（{rsi:.0f}）已接近超買（70），留意過熱拉回風險")
        elif 30 < rsi <= 35:
            scenarios.append(f"RSI（{rsi:.0f}）已接近超賣（30），留意止跌反彈機會")

    k, d, kd_cross = t.get("k"), t.get("d"), t.get("kd_cross")
    if k is not None and d is not None and kd_cross is None:
        diff = k - d
        if 0 < diff <= 5:
            scenarios.append(f"K值（{k:.0f}）僅略高於D值（{d:.0f}），留意是否即將翻轉為死亡交叉")
        elif -5 <= diff < 0:
            scenarios.append(f"K值（{k:.0f}）僅略低於D值（{d:.0f}），留意是否即將翻轉為黃金交叉")

    return scenarios


# ─────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────

def fetch_taiex() -> dict:
    result = {
        "date": None,
        "close": None,
        "change": None,
        "change_pct": None,
        "rsi": None,
        "rsi_reading": None,
        "macd": None,
        "macd_signal": None,
        "macd_hist": None,
        "macd_cross": None,
        "k": None,
        "d": None,
        "kd_reading": None,
        "kd_cross": None,
        "boll_upper": None,
        "boll_mid": None,
        "boll_lower": None,
        "boll_reading": None,
        "ma5": None,
        "ma20": None,
        "ma60": None,
        "ma5_20_cross": None,
        "ma20_60_cross": None,
        "ma_alignment": None,
        "recent_high_20d": None,
        "recent_low_20d": None,
        "narrative": None,
        "scenarios": [],
        "error": None,
    }

    try:
        import yfinance as yf
        df = yf.Ticker(TAIEX_TICKER).history(period="1y")
        if df is None or df.empty:
            raise ValueError("yfinance returned empty history for ^TWII")

        close, high, low = df["Close"], df["High"], df["Low"]

        rsi = _rsi(close, 14)
        macd_line, signal_line, hist = _macd(close, 12, 26, 9)
        k, d = _kd(high, low, close, 9)
        boll_mid, boll_upper, boll_lower = _bollinger(close, 20, 2)
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()

        last = df.index[-1]
        result["date"] = last.strftime("%Y/%m/%d")
        result["close"] = round(float(close.iloc[-1]), 2)
        if len(close) >= 2:
            prev_close = float(close.iloc[-2])
            result["change"] = round(result["close"] - prev_close, 2)
            result["change_pct"] = round((result["close"] - prev_close) / prev_close * 100, 2)

        result["rsi"] = round(float(rsi.iloc[-1]), 1) if not pd.isna(rsi.iloc[-1]) else None
        result["rsi_reading"] = _rsi_reading(rsi.iloc[-1])

        result["macd"] = round(float(macd_line.iloc[-1]), 2)
        result["macd_signal"] = round(float(signal_line.iloc[-1]), 2)
        result["macd_hist"] = round(float(hist.iloc[-1]), 2)
        result["macd_cross"] = _cross_event(macd_line, signal_line)

        result["k"] = round(float(k.iloc[-1]), 1) if not pd.isna(k.iloc[-1]) else None
        result["d"] = round(float(d.iloc[-1]), 1) if not pd.isna(d.iloc[-1]) else None
        result["kd_reading"] = _kd_reading(k.iloc[-1])
        result["kd_cross"] = _cross_event(k, d)

        result["boll_upper"] = round(float(boll_upper.iloc[-1]), 1) if not pd.isna(boll_upper.iloc[-1]) else None
        result["boll_mid"] = round(float(boll_mid.iloc[-1]), 1) if not pd.isna(boll_mid.iloc[-1]) else None
        result["boll_lower"] = round(float(boll_lower.iloc[-1]), 1) if not pd.isna(boll_lower.iloc[-1]) else None
        result["boll_reading"] = _boll_reading(close.iloc[-1], boll_upper.iloc[-1], boll_lower.iloc[-1])

        result["ma5"] = round(float(ma5.iloc[-1]), 1) if not pd.isna(ma5.iloc[-1]) else None
        result["ma20"] = round(float(ma20.iloc[-1]), 1) if not pd.isna(ma20.iloc[-1]) else None
        result["ma60"] = round(float(ma60.iloc[-1]), 1) if not pd.isna(ma60.iloc[-1]) else None
        result["ma5_20_cross"] = _cross_event(ma5, ma20)
        result["ma20_60_cross"] = _cross_event(ma20, ma60)
        result["ma_alignment"] = _ma_alignment(ma5.iloc[-1], ma20.iloc[-1], ma60.iloc[-1])

        result["recent_high_20d"], result["recent_low_20d"] = _recent_high_low(high, low, 20)

        result["narrative"] = _build_narrative(result)
        result["scenarios"] = _build_scenarios(result)

        logger.info("TAIEX fetched OK: date=%s close=%s", result["date"], result["close"])

    except Exception as exc:
        logger.warning("TAIEX fetch failed: %s", exc)
        result["error"] = str(exc)

    return result


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(fetch_taiex(), ensure_ascii=False, indent=2, default=str))
