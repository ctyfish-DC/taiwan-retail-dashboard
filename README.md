# taiwan-retail-dashboard

台灣零售業數據自動抓取 + LINE 推播。

## 排程（GitHub Actions）

| Workflow | 時間（台灣） | 內容 |
|----------|--------------|------|
| `weekly_notify.yml` | 每週一 08:00 | CPI 通膨率（IMF）＋ 個股營運（寶島光學 5312、寶利徠 1813）：股價/市值/獲利率取自 Yahoo Finance，近四季與今年累計營收取自 MOPS 月營收（經 FinMind API，境外可用） |
| `weekly_eyewear_notify.yml` | 每週三 10:00 | Eyewear Intelligence 新聞（只推播上次之後的新文章） |

兩者皆可在 Actions 頁面手動觸發（Run workflow）。

## 檔案結構

```
scripts/
  main.py            週報進入點：抓資料 → 組訊息 → LINE 推播
  fetch_data.py      CPI（IMF/World Bank）、個股 5312/1813（FinMind 月營收 + yfinance）
  send_line.py       LINE Messaging API 推播 + 訊息格式
  eyewear_notify.py  眼鏡新聞週報進入點
  fetch_eyewear.py   ewintelligence.com 爬蟲（以文章 ID 追蹤新舊）
data/
  eyewear_state.json 眼鏡新聞已讀狀態（由 Actions 自動 commit）
```

## GitHub Secrets

- `LINE_TOKEN` — LINE Messaging API channel access token
- `LINE_USER_ID` — 推播對象的 user ID（U 開頭）

## 已知限制

- **MOEA 經濟部零售業數據**：台灣政府網站（moea.gov.tw、data.gov.tw、mops.twse.com.tw、tpex.org.tw）封鎖境外 IP，GitHub Actions（美國）無法抓取。待架設台灣 IP 的 self-hosted runner 後啟用。
- **個股營收**：主來源為 MOPS 月營收（經 FinMind 鏡像，最新）；FinMind 失效時退回 Yahoo 季報（台股中小型股更新較慢，會標注「可能落後」）。毛利率/稅後淨利仍取自 Yahoo，可能落後最新季。
