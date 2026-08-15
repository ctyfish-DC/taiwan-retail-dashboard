"""
taiex_notify.py
Orchestrates fetching 台灣加權指數 (^TWII) technical indicators and pushing
a daily LINE report, reusing the same LINE push helper as main.py.

Required environment variables:
    LINE_TOKEN    — LINE channel access token
    LINE_USER_ID  — LINE user ID to push to
"""

import logging
import sys
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


def _fmt_pts(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:,.2f}"


def build_message(t: dict) -> str:
    if t.get("error") and t.get("close") is None:
        return (
            f"📈 台灣加權指數技術分析\n\n"
            f"⚠️ 資料暫時無法取得: {t['error']}\n\n"
            f"─────────────────\n"
            f"資料來源: Yahoo Finance"
        )

    lines = [f"📈 台灣加權指數技術分析 {t.get('date', '')}", ""]
    lines.append(f"指數: {t['close']:,.2f} 點（{_fmt_pts(t.get('change'))}，{_fmt_pct(t.get('change_pct'))}）")
    lines.append("")

    lines.append("【動能指標】")
    if t.get("rsi") is not None:
        lines.append(f"RSI(14): {t['rsi']:.1f}（{t.get('rsi_reading', 'N/A')}）")
    if t.get("macd") is not None:
        cross_note = ""
        if t.get("macd_cross") == "golden":
            cross_note = "　黃金交叉"
        elif t.get("macd_cross") == "death":
            cross_note = "　死亡交叉"
        lines.append(f"MACD: {t['macd']:.1f} / 訊號線 {t['macd_signal']:.1f} / 柱狀 {t['macd_hist']:.1f}{cross_note}")
    if t.get("k") is not None:
        cross_note = ""
        if t.get("kd_cross") == "golden":
            cross_note = "　黃金交叉"
        elif t.get("kd_cross") == "death":
            cross_note = "　死亡交叉"
        lines.append(f"KD: K {t['k']:.1f} / D {t['d']:.1f}（{t.get('kd_reading', 'N/A')}）{cross_note}")
    lines.append("")

    lines.append("【波動 / 均線】")
    if t.get("boll_upper") is not None:
        lines.append(
            f"布林通道(20,2): {t.get('boll_reading', 'N/A')}"
            f"（上緣 {t['boll_upper']:,.0f} / 下緣 {t['boll_lower']:,.0f}）"
        )
    if t.get("ma5") is not None and t.get("ma20") is not None and t.get("ma60") is not None:
        lines.append(
            f"均線: MA5 {t['ma5']:,.0f} / MA20 {t['ma20']:,.0f} / MA60 {t['ma60']:,.0f}"
            f"（{t.get('ma_alignment', 'N/A')}）"
        )
    lines.append("")

    if t.get("narrative"):
        lines.append("【摘要】")
        lines.append(t["narrative"])
        lines.append("")

    lines.append("─────────────────")
    lines.append("資料來源: Yahoo Finance (^TWII)")
    lines.append("⚠️ 僅為技術面數據整理，非投資建議")

    return "\n".join(lines)


def main() -> int:
    try:
        from fetch_taiex import fetch_taiex
        from send_line import send_line_message
    except ImportError as exc:
        logger.error("Import error: %s", exc)
        return 1

    logger.info("=== TAIEX Daily Technical Notify — starting ===")

    data = fetch_taiex()
    logger.info("Data fetch complete.")

    message = build_message(data)
    logger.info("Message built (%d chars).", len(message))

    print("\n=== Message Preview ===")
    print(message)
    print("=======================\n")

    if send_line_message(message):
        logger.info("LINE message sent successfully.")
        return 0
    logger.error("Failed to send LINE message.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
