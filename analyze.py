"""
Baby-name sensitivity analysis.

For each study name, compute:
  - full SSA time series (count and rank within sex)
  - a 5-year pre-event linear trend (in log-count space and rank space)
  - projected counterfactual trajectory for 5 years post-event
  - deviation (actual - projected) each year through t+5

Output:
  - data tables (CSV) with series, trends, and deviations
  - per-name trajectory PNG chart (log-count)
  - main comparison PNG chart: deviation curves overlaid, colored by bucket

Data source: SSA national yob{YEAR}.txt files in data/national/
(Mirrored from the SSA "Beyond the Top 1000 Names" data set.)
"""

from __future__ import annotations

import os
import csv
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "national")
OUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUT_DIR, exist_ok=True)


# ----------------------------------------------------------------------------
# Load SSA data
# ----------------------------------------------------------------------------
def load_all_years() -> pd.DataFrame:
    frames = []
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.startswith("yob") or not fname.endswith(".txt"):
            continue
        year = int(fname[3:7])
        df = pd.read_csv(
            os.path.join(DATA_DIR, fname),
            header=None,
            names=["name", "sex", "count"],
            dtype={"name": "string", "sex": "string", "count": "int64"},
        )
        df["year"] = year
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)
    # Within-sex, within-year rank (1 = most popular). Ties get min rank.
    all_df["rank"] = (
        all_df.groupby(["year", "sex"])["count"]
        .rank(method="min", ascending=False)
        .astype(int)
    )
    return all_df


# ----------------------------------------------------------------------------
# Case list
# ----------------------------------------------------------------------------
@dataclass
class Case:
    name: str
    sex: str
    event_year: int
    bucket: str  # "sensitive", "celebrity", "brand"
    note: str = ""


CASES: List[Case] = [
    # Sensitive-issue association
    Case("Isis",     "F", 2014, "sensitive", "ISIS/Daesh terrorism coverage"),
    Case("Monica",   "F", 1998, "sensitive", "Lewinsky scandal"),
    Case("Katrina",  "F", 2005, "sensitive", "Hurricane Katrina"),
    Case("Ellen",    "F", 1997, "sensitive", "DeGeneres coming-out episode"),
    Case("Donald",   "M", 2015, "sensitive", "Trump 2016 campaign ramp-up"),
    Case("Hillary",  "F", 2016, "sensitive", "Clinton general election run"),
    Case("Karen",    "F", 2018, "sensitive", "'Karen' pejorative meme peak"),
    # Brand association (not a sensitive issue)
    Case("Alexa",    "F", 2014, "brand",     "Amazon Echo launch"),
    # Non-sensitive celebrity association (positive / neutral valence)
    Case("Miley",    "F", 2013, "celebrity", "Cyrus VMAs peak tabloid year"),
    Case("Ariana",   "F", 2014, "celebrity", "Grande pop stardom"),
    Case("Khaleesi", "F", 2012, "celebrity", "GoT cultural phenomenon"),
    Case("Adele",    "F", 2011, "celebrity", "'21' blockbuster album"),
]


# Previous Clinton-era run-up for Hillary means a 5-year pre-event trend ending
# 2015 is dominated by the POST-2008 decline. For the 2016 event we want the
# counterfactual to be "whatever trajectory it was on in the quiet years
# leading up to 2016", so leave as-is but flag it in the output.


# ----------------------------------------------------------------------------
# Per-name analysis
# ----------------------------------------------------------------------------
PRE_WINDOW = 5   # 5 years before event (event-5 .. event-1)
POST_WINDOW = 5  # project 5 years after event


def name_series(all_df: pd.DataFrame, name: str, sex: str) -> pd.DataFrame:
    """Return a dataframe indexed by year with columns count, rank.
    Years where the name did not appear in the SSA file (i.e. fewer than 5
    babies of that sex nationally) are filled with count=0 and rank=NaN."""
    sub = all_df[(all_df["name"] == name) & (all_df["sex"] == sex)].set_index("year")
    years = range(all_df["year"].min(), all_df["year"].max() + 1)
    out = pd.DataFrame(index=list(years))
    out["count"] = sub["count"].reindex(out.index).fillna(0).astype(int)
    out["rank"] = sub["rank"].reindex(out.index)  # leave NaN where absent
    out.index.name = "year"
    return out


def fit_trend_and_project(
    series: pd.DataFrame,
    event_year: int,
    pre: int = PRE_WINDOW,
    post: int = POST_WINDOW,
) -> pd.DataFrame:
    """Fit linear trends on log(count+1) and on rank over pre years ending the
    year before event_year. Project forward post years. Returns a dataframe
    spanning [event_year-pre, event_year+post] with columns:
      count, rank, log_count, proj_log_count, dev_log_count,
      proj_rank, dev_rank
    dev_log_count = actual - projected (units: log; positive = out-performed).
    dev_rank      = projected - actual (positive = actual rank number is LOWER,
                    i.e. more popular than projected; negative = underperformed).
    """
    pre_years = list(range(event_year - pre, event_year))
    full_years = list(range(event_year - pre, event_year + post + 1))
    s = series.loc[full_years].copy()
    s["log_count"] = np.log(s["count"] + 1.0)

    # log-count trend
    x_pre = np.array(pre_years, dtype=float)
    y_pre = s.loc[pre_years, "log_count"].values
    slope, intercept = np.polyfit(x_pre, y_pre, 1)
    s["proj_log_count"] = slope * np.array(full_years) + intercept
    s["dev_log_count"] = s["log_count"] - s["proj_log_count"]

    # rank trend (only if ranks are available in pre-window)
    r_pre = s.loc[pre_years, "rank"].values
    if not np.any(pd.isna(r_pre)):
        r_slope, r_intercept = np.polyfit(x_pre, r_pre, 1)
        s["proj_rank"] = r_slope * np.array(full_years) + r_intercept
        s["dev_rank"] = s["proj_rank"] - s["rank"]  # flipped so +ve = better
    else:
        s["proj_rank"] = np.nan
        s["dev_rank"] = np.nan

    s.index.name = "year"
    return s


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
BUCKET_COLORS = {
    "sensitive": "#c0392b",   # red
    "celebrity": "#2980b9",   # blue
    "brand":     "#8e44ad",   # purple
}


def main() -> None:
    print("Loading SSA yearly files ...")
    all_df = load_all_years()
    yr_min, yr_max = all_df["year"].min(), all_df["year"].max()
    print(f"Loaded {len(all_df):,} rows, years {yr_min}-{yr_max}")

    rows_long: List[Dict] = []
    summary_rows: List[Dict] = []
    dev_curves: Dict[str, np.ndarray] = {}       # t -> dev_log_count[t-0..+5]
    rank_curves: Dict[str, np.ndarray] = {}

    per_name_series: Dict[str, pd.DataFrame] = {}

    for case in CASES:
        series = name_series(all_df, case.name, case.sex)
        fit = fit_trend_and_project(series, case.event_year)
        per_name_series[case.name] = fit

        # Long-form dump for CSV output
        for yr, r in fit.iterrows():
            rows_long.append({
                "name": case.name, "sex": case.sex, "bucket": case.bucket,
                "event_year": case.event_year, "year": yr,
                "t": yr - case.event_year,
                "count": int(r["count"]),
                "rank": (None if pd.isna(r["rank"]) else int(r["rank"])),
                "log_count": r["log_count"],
                "proj_log_count": r["proj_log_count"],
                "dev_log_count": r["dev_log_count"],
                "proj_rank": (None if pd.isna(r["proj_rank"]) else float(r["proj_rank"])),
                "dev_rank": (None if pd.isna(r["dev_rank"]) else float(r["dev_rank"])),
            })

        # Summary at t+1, t+3, t+5
        def pct_dev(log_dev: float) -> float:
            return (np.exp(log_dev) - 1.0) * 100.0

        for t in (1, 3, 5):
            yr = case.event_year + t
            if yr > yr_max:
                continue
            row = fit.loc[yr]
            summary_rows.append({
                "name": case.name, "bucket": case.bucket,
                "event_year": case.event_year, "t_plus": t,
                "year": yr,
                "actual_count": int(row["count"]),
                "proj_count": float(np.exp(row["proj_log_count"]) - 1.0),
                "dev_log_count": float(row["dev_log_count"]),
                "pct_deviation": pct_dev(float(row["dev_log_count"])),
                "actual_rank": (None if pd.isna(row["rank"]) else int(row["rank"])),
                "proj_rank": (None if pd.isna(row["proj_rank"]) else float(row["proj_rank"])),
                "rank_deviation": (None if pd.isna(row["dev_rank"]) else float(row["dev_rank"])),
            })

        # Normalized deviation curve indexed at event year (t=0)
        ts = list(range(0, POST_WINDOW + 1))
        yrs = [case.event_year + t for t in ts]
        log_base = fit.loc[case.event_year, "dev_log_count"]
        dev_curves[case.name] = np.array(
            [fit.loc[y, "dev_log_count"] - log_base for y in yrs]
        )
        if fit["dev_rank"].notna().all():
            rank_base = fit.loc[case.event_year, "dev_rank"]
            rank_curves[case.name] = np.array(
                [fit.loc[y, "dev_rank"] - rank_base for y in yrs]
            )

    # --- Write CSV outputs ---
    pd.DataFrame(rows_long).to_csv(
        os.path.join(OUT_DIR, "series_long.csv"), index=False
    )
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(os.path.join(OUT_DIR, "summary.csv"), index=False)

    # --- Console summary table ---
    print("\n=== Deviation from pre-event trend (log-count space) ===")
    print("     name  bucket       event   t+1 %   t+3 %   t+5 %")
    for case in CASES:
        parts = [f"{case.name:>8}  {case.bucket:<9}  {case.event_year}"]
        for t in (1, 3, 5):
            row = summary_df[
                (summary_df["name"] == case.name)
                & (summary_df["t_plus"] == t)
            ]
            if len(row):
                parts.append(f"{row['pct_deviation'].iloc[0]:+7.1f}")
            else:
                parts.append("    n/a")
        print("   ".join(parts))

    # --- Plot 1: per-name trajectory (log-count) grid ---
    n = len(CASES)
    ncols = 3
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, 3.2 * nrows), sharex=False)
    axes = axes.flatten()
    for i, case in enumerate(CASES):
        ax = axes[i]
        full = per_name_series[case.name]
        series = name_series(all_df, case.name, case.sex)
        # full history, limited to 1950+ for readability
        hist = series.loc[max(1950, yr_min):yr_max]
        ax.plot(hist.index, hist["count"], color="#333", lw=1.2, label="actual")
        # projection line
        proj_years = full.index
        proj_counts = np.exp(full["proj_log_count"]) - 1.0
        ax.plot(
            proj_years, proj_counts,
            linestyle="--", color=BUCKET_COLORS[case.bucket], lw=1.8,
            label="pre-event trend projected",
        )
        ax.axvline(case.event_year, color="gray", lw=0.8, linestyle=":")
        ax.set_title(f"{case.name} ({case.sex})  —  {case.bucket}  event {case.event_year}")
        ax.set_yscale("log")
        ax.set_ylabel("babies / yr")
        ax.grid(True, which="both", alpha=0.25)
        if i == 0:
            ax.legend(loc="upper left", fontsize=8)
    for j in range(len(CASES), len(axes)):
        axes[j].axis("off")
    fig.suptitle(
        "Annual US births — actual vs. counterfactual (pre-event linear trend in log-count)",
        fontsize=13,
    )
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "per_name_trajectories.png"), dpi=150)
    plt.close(fig)

    # --- Plot 2: main deviation comparison ---
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))
    ts = np.arange(0, POST_WINDOW + 1)

    # log deviation panel
    for case in CASES:
        curve = dev_curves[case.name]
        pct = (np.exp(curve) - 1.0) * 100.0
        ax1.plot(
            ts, pct, marker="o",
            color=BUCKET_COLORS[case.bucket], alpha=0.85,
            label=f"{case.name} ({case.bucket})",
        )
    ax1.axhline(0, color="k", lw=0.8)
    ax1.set_xlabel("years after event")
    ax1.set_ylabel("% deviation from pre-event trend (count)")
    ax1.set_title("Deviation in count space (log-trend counterfactual)")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8, ncol=2)

    # rank deviation panel
    for case in CASES:
        if case.name not in rank_curves:
            continue
        curve = rank_curves[case.name]
        ax2.plot(
            ts, curve, marker="o",
            color=BUCKET_COLORS[case.bucket], alpha=0.85,
            label=f"{case.name} ({case.bucket})",
        )
    ax2.axhline(0, color="k", lw=0.8)
    ax2.set_xlabel("years after event")
    ax2.set_ylabel("rank-positions relative to projection (+ = more popular)")
    ax2.set_title("Deviation in rank space")
    ax2.grid(alpha=0.3)
    ax2.invert_yaxis()  # so 'dropped in popularity' goes down
    ax2.legend(fontsize=8, ncol=2)

    fig.suptitle(
        "Name trajectory deviations after association events, indexed to t=0",
        fontsize=13,
    )
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "deviation_comparison.png"), dpi=150)
    plt.close(fig)

    # --- Plot 3: bucket average deviation ---
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for bucket in ("sensitive", "celebrity", "brand"):
        bucket_names = [c.name for c in CASES if c.bucket == bucket]
        if not bucket_names:
            continue
        stack = np.array([dev_curves[n] for n in bucket_names])
        mean = stack.mean(axis=0)
        pct = (np.exp(mean) - 1.0) * 100.0
        ax.plot(
            ts, pct, marker="o", lw=2.5,
            color=BUCKET_COLORS[bucket],
            label=f"{bucket} (n={len(bucket_names)})",
        )
        # Light individual lines for reference
        for n in bucket_names:
            ax.plot(
                ts, (np.exp(dev_curves[n]) - 1.0) * 100.0,
                color=BUCKET_COLORS[bucket], alpha=0.25, lw=1,
            )
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("years after event")
    ax.set_ylabel("% deviation from pre-event trend (count)")
    ax.set_title("Bucket-average deviation (bold) vs. individual names (faded)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "bucket_average.png"), dpi=150)
    plt.close(fig)

    print(f"\nWrote outputs to {OUT_DIR}/")


if __name__ == "__main__":
    main()
