# taiwan-retail-dashboard

台灣零售股（寶島光學 5312、寶利徠 1813）月營收、台灣加權指數技術分析，自動抓取 + LINE 推播。

## 排程（GitHub Actions）

| Workflow | 時間（台灣） | 內容 |
|----------|--------------|------|
| `daily_taiex_notify.yml` | 每個交易日 15:00 | 台灣加權指數 (^TWII) 技術分析：RSI(14)、MACD(12,26,9)、KD(9,3,3)、布林通道(20,2)、MA5/20/60 均線交叉，附規則式文字摘要與「情境觀察」（月線/季線得失、近期高低點、指標接近轉折時的條件式提醒） |
| `monthly_notify.yml` | 每月 11 日 09:00 | 個股最新月營收（寶島光學 5312、寶利徠 1813）：股價/市值取自 Yahoo Finance；月營收（單位：萬元，含 MoM/YoY%）、近半年趨勢、今年累計取自 MOPS 月營收（經 FinMind API，境外可用） |
| `weekly_eyewear_notify.yml` | 每週三 10:00 | Eyewear Intelligence 新聞（只推播上次之後的新文章） |

三者皆可在 Actions 頁面手動觸發（Run workflow）。

月營收排程訂在 11 日，是因為台灣上市/上櫃公司依規定須於**次月 10 日前**公告月營收，留一天緩衝確保資料已公告。若當月公告延後（例如遇連假），程式會自動顯示「資料裡最新的一個月」，不會出錯，下個月會自動補上。

台灣加權指數排程固定週一至週五執行，不特別判斷國定假日；遇假日當天沒有新收盤價時，會顯示上一個交易日的資料（沒有狀態追蹤跳過機制，每個排定時間都會推播）。

## 檔案結構

```
scripts/
  main.py            月營收月報進入點：抓資料 → 組訊息 → LINE 推播
  fetch_data.py      個股月營收 5312/1813（FinMind + yfinance）
  send_line.py       LINE Messaging API 推播（共用）+ 月營收訊息格式
  taiex_notify.py     加權指數技術分析日報進入點 + 訊息格式
  fetch_taiex.py      加權指數 OHLC（yfinance）+ 技術指標計算（純 pandas/numpy）
  eyewear_notify.py   眼鏡新聞週報進入點
  fetch_eyewear.py    ewintelligence.com 爬蟲（以文章 ID 追蹤新舊）
data/
  eyewear_state.json  眼鏡新聞已讀狀態（由 Actions 自動 commit）
```

## GitHub Secrets

- `LINE_TOKEN` — LINE Messaging API channel access token
- `LINE_USER_ID` — 推播對象的 user ID（U 開頭）

## 已知限制

- **技術指標非投資建議**：`fetch_taiex.py` 裡的判讀（超買/超賣、黃金/死亡交叉等）皆為固定數字門檻的規則判斷，不涉及任何 AI 或人工研判，僅供參考。
- **「情境觀察」不是價格預測**：`_build_scenarios()` 只列出「目前站在哪個關鍵價位的哪一側、若跨過該價位技術面通常代表什麼」的條件式句子（例如：站上月線、若跌破則短期轉弱訊號浮現），純規則生成，不對未來走勢做任何判斷或機率評估。
- **技術指標套件選擇**：刻意不使用 TA-Lib（需編譯 C 函式庫，CI 環境安裝常見問題）或 pandas-ta（上游專案已進入停止維護狀態），改用純 pandas/numpy 手刻 RSI/MACD/KD/布林通道，已離線驗證公式正確性（RSI/KD 值域、MACD 手動核對、交叉事件偵測等）。
- **無官方月度損益資料**：台灣法規僅強制公告「月營收」，毛利率／稅後淨利等損益數字依規定只在季報（季結後 1 個月）、半年報（2 個月）、年報（4 個月）揭露，沒有任何官方來源提供月度損益。因此週報裡的毛利率/稅後淨利一律標示為「最近一季」（來自 Yahoo Finance），並附上文字說明，避免誤讀為月度數字。營收趨勢摘要（月增減、連續年增/減月數）則是完全基於官方月營收數字計算，屬月度且可信。
- **個股月營收**：主來源為 MOPS 月營收（經 FinMind 鏡像，可拆到單月，含 MoM/YoY%）；FinMind 失效時退回 Yahoo 季報（無法拆到單月，且台股中小型股更新較慢，會標注「可能落後」）。
- **CPI（消費者物價指數）**：已移除。IMF/World Bank 對台灣僅提供年度資料，無法反映月度物價變化，資訊價值不足。
- **整體零售業（MOEA）數據**：台灣政府網站（moea.gov.tw、data.gov.tw、mops.twse.com.tw）封鎖境外 IP，且查無其他可靠的全球可用替代來源（FinMind 無此資料集、FRED 未收錄台灣零售序列），已停用此項目。
