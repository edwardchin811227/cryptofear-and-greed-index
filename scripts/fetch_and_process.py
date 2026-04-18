from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests


API_URL = "https://api.alternative.me/fng/?limit=0&format=json"
REPO = Path(__file__).resolve().parents[1]
DATA_FILE = REPO / "data" / "fng.csv"
JSON_FILE = REPO / "docs" / "fng_data.json"
CSV_COLUMNS = ["date", "timestamp", "value", "classification"]


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "cryptofng-updater/1.0"})
    return session


def _fetch_api_data() -> pd.DataFrame:
    session = _session()
    response = session.get(API_URL, timeout=40)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data", [])
    if not rows:
        raise RuntimeError("Alternative.me returned empty data")

    parsed_rows: list[dict[str, object]] = []
    for row in rows:
        ts_raw = row.get("timestamp")
        value_raw = row.get("value")
        if ts_raw is None or value_raw is None:
            continue

        ts = int(ts_raw)
        value = pd.to_numeric(value_raw, errors="coerce")
        if pd.isna(value):
            continue

        date_utc = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        parsed_rows.append(
            {
                "date": date_utc,
                "timestamp": ts,
                "value": float(value),
                "classification": str(row.get("value_classification", "")).strip(),
            }
        )

    if not parsed_rows:
        raise RuntimeError("No valid rows parsed from Alternative.me")

    df = pd.DataFrame(parsed_rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "timestamp", "value"]).copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    df = df.sort_values(["date", "timestamp"]).drop_duplicates(subset=["date"], keep="last")
    return df[CSV_COLUMNS].reset_index(drop=True)


def _load_existing_csv() -> pd.DataFrame:
    if not DATA_FILE.exists():
        return pd.DataFrame(columns=CSV_COLUMNS)

    raw = pd.read_csv(DATA_FILE)
    if raw.empty:
        return pd.DataFrame(columns=CSV_COLUMNS)

    df = raw.copy()

    if "value_classification" in df.columns and "classification" not in df.columns:
        df = df.rename(columns={"value_classification": "classification"})

    if "date" not in df.columns:
        if "Date" in df.columns:
            df = df.rename(columns={"Date": "date"})
        elif "timestamp" in df.columns:
            ts_dt = pd.to_datetime(pd.to_numeric(df["timestamp"], errors="coerce"), unit="s", utc=True)
            df["date"] = ts_dt.dt.strftime("%Y-%m-%d")
        else:
            raise ValueError("data/fng.csv must have either date or timestamp column")

    if "timestamp" not in df.columns:
        dt_col = pd.to_datetime(df["date"], errors="coerce", utc=True)
        df["timestamp"] = (dt_col.view("int64") // 1_000_000_000).astype("Int64")

    if "value" not in df.columns:
        raise ValueError("data/fng.csv missing value column")

    if "classification" not in df.columns:
        df["classification"] = ""

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["classification"] = df["classification"].fillna("").astype(str)
    df = df.dropna(subset=["date", "timestamp", "value"]).copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    df = df.sort_values(["date", "timestamp"]).drop_duplicates(subset=["date"], keep="last")
    return df[CSV_COLUMNS].reset_index(drop=True)


def _merge_with_backfill(existing: pd.DataFrame, fresh: pd.DataFrame, backfill_days: int) -> pd.DataFrame:
    if existing.empty:
        return fresh.copy()

    if fresh.empty:
        return existing.copy()

    fresh_dates = pd.to_datetime(fresh["date"], errors="coerce")
    latest_date = fresh_dates.max().date()
    cutoff_date = latest_date - timedelta(days=max(backfill_days, 0))

    existing_dates = pd.to_datetime(existing["date"], errors="coerce").dt.date
    fresh_dates_only = fresh_dates.dt.date

    existing_old = existing.loc[existing_dates < cutoff_date].copy()
    fresh_recent = fresh.loc[fresh_dates_only >= cutoff_date].copy()
    fresh_old_fill = fresh.loc[(fresh_dates_only < cutoff_date) & (~fresh["date"].isin(existing_old["date"]))].copy()

    merged = pd.concat([existing_old, fresh_old_fill, fresh_recent], ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"], errors="coerce")
    merged["timestamp"] = pd.to_numeric(merged["timestamp"], errors="coerce")
    merged["value"] = pd.to_numeric(merged["value"], errors="coerce")
    merged["classification"] = merged["classification"].fillna("").astype(str)
    merged = merged.dropna(subset=["date", "timestamp", "value"])
    merged["date"] = merged["date"].dt.strftime("%Y-%m-%d")
    merged = merged.sort_values(["date", "timestamp"]).drop_duplicates(subset=["date"], keep="last")
    return merged[CSV_COLUMNS].reset_index(drop=True)


def _value_or_none(value: object, *, digits: int = 6) -> float | int | None:
    if pd.isna(value):
        return None
    num = float(value)
    if not math.isfinite(num):
        return None
    rounded = round(num, digits)
    if float(rounded).is_integer():
        return int(rounded)
    return rounded


def _build_enriched_json_rows(df: pd.DataFrame) -> list[dict[str, object]]:
    calc = df.copy()
    calc["value"] = pd.to_numeric(calc["value"], errors="coerce")
    calc = calc.sort_values("date").reset_index(drop=True)

    calc["mean250"] = calc["value"].rolling(window=250, min_periods=250).mean()
    calc["std250"] = calc["value"].rolling(window=250, min_periods=250).std(ddof=1)

    for n in (1, 2, 3):
        calc[f"upper{n}"] = calc["mean250"] + n * calc["std250"]
        calc[f"lower{n}"] = calc["mean250"] - n * calc["std250"]

    json_rows: list[dict[str, object]] = []
    for _, row in calc.iterrows():
        json_rows.append(
            {
                "date": str(row["date"]),
                "value": _value_or_none(row["value"], digits=4),
                "classification": str(row.get("classification", "")),
                "mean250": _value_or_none(row["mean250"], digits=6),
                "std250": _value_or_none(row["std250"], digits=6),
                "upper1": _value_or_none(row["upper1"], digits=6),
                "lower1": _value_or_none(row["lower1"], digits=6),
                "upper2": _value_or_none(row["upper2"], digits=6),
                "lower2": _value_or_none(row["lower2"], digits=6),
                "upper3": _value_or_none(row["upper3"], digits=6),
                "lower3": _value_or_none(row["lower3"], digits=6),
            }
        )

    return json_rows


def _to_csv_text(df: pd.DataFrame) -> str:
    out = df.copy()
    out = out[CSV_COLUMNS].sort_values("date").reset_index(drop=True)
    out["timestamp"] = pd.to_numeric(out["timestamp"], errors="coerce").astype("Int64")
    out["value"] = pd.to_numeric(out["value"], errors="coerce").round(6)
    buffer = StringIO()
    out.to_csv(buffer, index=False, lineterminator="\n")
    return buffer.getvalue()


def _to_json_text(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _write_if_changed(path: Path, content: str) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    if existing == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and process Alternative.me Fear & Greed data")
    parser.add_argument("--backfill-days", type=int, default=7, help="Reconcile last N days with fresh API data")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.backfill_days < 0:
        raise ValueError("--backfill-days must be >= 0")

    existing = _load_existing_csv()
    fresh = _fetch_api_data()
    merged = _merge_with_backfill(existing, fresh, args.backfill_days)

    if merged.empty:
        raise RuntimeError("Merged dataset is empty after processing")

    json_rows = _build_enriched_json_rows(merged)
    target_date = str(merged["date"].iloc[-1])
    payload = {
        "updated": target_date,
        "rows": json_rows,
    }

    csv_changed = _write_if_changed(DATA_FILE, _to_csv_text(merged))
    json_changed = _write_if_changed(JSON_FILE, _to_json_text(payload))

    print(f"target_date={target_date}")
    print(
        f"rows: existing={len(existing)} fresh={len(fresh)} merged={len(merged)} "
        f"| backfill_days={args.backfill_days}"
    )
    print(f"changed: csv={csv_changed} json={json_changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

