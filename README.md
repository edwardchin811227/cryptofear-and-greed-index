# Crypto Fear & Greed Index (250MA ±σ)

這個專案會每天抓取 Alternative.me 的 Crypto Fear & Greed Index，計算 250 日移動平均與 ±1/±2/±3 標準差區間，輸出到 GitHub Pages 靜態圖表。

## 專案結構

```text
cryptofear-and-greed-index/
├── data/
│   └── fng.csv
├── docs/
│   ├── index.html
│   └── fng_data.json
├── scripts/
│   ├── fetch_and_process.py
│   ├── publish_data.ps1
│   ├── run_daily.ps1
│   └── install_daily_task.ps1
└── requirements.txt
```

## 安裝

```powershell
python -m pip install -r requirements.txt
```

## 手動更新資料

```powershell
python scripts/fetch_and_process.py --backfill-days 7
```

會更新：

- `data/fng.csv`
- `docs/fng_data.json`

## 本機自動化流程（比照 usmarket）

### 1) 一次執行更新 + 發布

```powershell
powershell scripts/run_daily.ps1
```

若要直接推送：

```powershell
powershell scripts/run_daily.ps1 -Push
```

### 2) 安裝每日排程（08:10）

```powershell
powershell scripts/install_daily_task.ps1 -RunAt 08:10
```

任務會執行：

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/run_daily.ps1 -Push -BackfillDays 7
```

## 資料更新時間

Alternative.me FNG 每日更新時間可觀測為 UTC 00:00（約台北/上海時間 08:00）。本專案排程設為 08:10，以避開更新邊界。

## GitHub Pages

在 GitHub repository 設定：

- `Settings -> Pages`
- Source: `Deploy from a branch`
- Branch: `main`
- Folder: `/docs`

部署網址：

```text
https://<username>.github.io/cryptofear-and-greed-index/
```

