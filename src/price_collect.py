"""Download three FRED energy price series into ``data/processed/00_external/``.

* ``DJFUELUSGULF`` — U.S. Gulf Coast jet fuel spot (USD/gallon).
* ``DCOILBRENTEU`` — Brent crude spot (USD/barrel).
* ``DCOILWTICO`` — WTI Cushing spot (USD/barrel).

Each series is written to its own CSV plus a combined long-format
``energy_price_timeline.csv``.  No raw stage — FRED publishes ready-to-use
CSVs.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_EXTERNAL_DIR = PROJECT_ROOT / "data" / "processed" / "00_external"

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

# Backwards-compat constant used by tests/test_price_collect.py.
FRED_SERIES_ID = "DJFUELUSGULF"


@dataclass(frozen=True)
class PriceSeries:
    series_id: str
    label: str
    units: str


SERIES: tuple[PriceSeries, ...] = (
    PriceSeries("DJFUELUSGULF", "US Gulf Coast jet fuel spot", "USD per gallon"),
    PriceSeries("DCOILBRENTEU", "Brent crude spot", "USD per barrel"),
    PriceSeries("DCOILWTICO", "WTI Cushing spot", "USD per barrel"),
)


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parse_fred_rows(
    rows: list[dict[str, str]],
    start_date: date,
    end_date: date,
    series_id: str = FRED_SERIES_ID,
) -> list[dict[str, str]]:
    """Filter raw FRED rows to ``start_date..end_date``, drop missing observations.

    Emits both ``value`` (unit-neutral) and ``price_usd_per_gallon``
    (legacy column kept only because the jet-fuel series is in USD/gal
    and the existing test relies on it).  Brent and WTI are in
    USD/barrel — callers should read ``value`` for them.
    """
    out: list[dict[str, str]] = []
    for row in rows:
        d = parse_date(row["observation_date"])
        value = row.get(series_id, ".")
        if start_date <= d <= end_date and value not in {"", "."}:
            out.append(
                {
                    "date": row["observation_date"],
                    "series_id": series_id,
                    "value": value,
                    # Legacy alias — accurate for DJFUELUSGULF only.
                    "price_usd_per_gallon": value,
                }
            )
    return out


def download_fred_rows(series_id: str) -> list[dict[str, str]]:
    url = FRED_CSV_URL.format(series_id=series_id)
    with urlopen(url, timeout=30) as response:  # noqa: S310 - FRED is a trusted public source
        text = response.read().decode("utf-8")
    return list(csv.DictReader(text.splitlines()))


def write_series_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["date", "series_id", "value", "price_usd_per_gallon"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_combined_csv(by_series: dict[str, list[dict[str, str]]], path: Path) -> None:
    meta = {s.series_id: s for s in SERIES}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["date", "series_id", "label", "units", "value"]
        )
        writer.writeheader()
        for series_id, rows in by_series.items():
            m = meta.get(series_id)
            for row in rows:
                writer.writerow(
                    {
                        "date": row["date"],
                        "series_id": series_id,
                        "label": m.label if m else series_id,
                        "units": m.units if m else "",
                        "value": row["price_usd_per_gallon"],
                    }
                )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def collect_price_timelines(
    start_date: date,
    end_date: date,
    output_dir: Path = PROCESSED_EXTERNAL_DIR,
) -> dict[str, list[dict[str, str]]]:
    """Pull every series, write per-series + combined CSVs, return rows."""
    by_series: dict[str, list[dict[str, str]]] = {}
    for series in SERIES:
        print(f"Fetching {series.series_id} ...")
        rows = parse_fred_rows(
            download_fred_rows(series.series_id),
            start_date,
            end_date,
            series_id=series.series_id,
        )
        path = output_dir / f"{series.series_id.lower()}_timeline.csv"
        write_series_csv(rows, path)
        by_series[series.series_id] = rows
        print(f"  {len(rows)} observations → {path}")
    combined = output_dir / "energy_price_timeline.csv"
    write_combined_csv(by_series, combined)
    print(f"Combined → {combined}")
    return by_series


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="2026-03-01")
    parser.add_argument("--end-date", default="2026-05-15")
    parser.add_argument("--output-dir", type=Path, default=PROCESSED_EXTERNAL_DIR)
    args = parser.parse_args(argv)
    collect_price_timelines(
        parse_date(args.start_date),
        parse_date(args.end_date),
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
