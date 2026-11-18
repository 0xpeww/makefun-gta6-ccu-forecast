"""Forecast Twitch viewership for hours 2-24 of a game-launch day.

Each case supplies hour 1 and a set of earlier launches.  For a comparable launch, the
model learns the median log-ratio

    log(viewers_h / viewers_1)

at every forecast hour.  Its prediction is a random-walk fan around that median:

    log(viewers_h / viewers_1) = median_ratio_h + z * c * sqrt(h - 1)

where ``c`` is the fitted spread (0.20) and ``z`` is the normal quantile for each of the
five requested levels.  Hours with fewer than five comparable observations use the house
baseline shape instead: ``p1 * exp(z * 0.15 * sqrt(h - 1))``.

The comparable pool is deliberately restricted to launches whose first hour is at least
max(20,000, 15% of the case's first hour).  Without that scale filter, a launch that
unlocked at, say, 05:00Z contributes a tiny, pre-release hour 1 and a sharply rising
curve.  Those ratios differ by an order of magnitude from an established launch day and
can move the median itself, not merely widen the uncertainty around it.

Inputs are JSON case files named ``<id>.in``; outputs are compact JSON files named
``<id>.out`` with exactly 23 hourly rows.  Only the Python standard library is used.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

FORECAST_HOURS: tuple[int, ...] = tuple(range(2, 25))
QUANTILE_LEVELS: tuple[float, ...] = (0.05, 0.25, 0.5, 0.75, 0.95)
QUANTILE_Z: tuple[float, ...] = (-1.6449, -0.6745, 0.0, 0.6745, 1.6449)

SPREAD_SCALE = 0.20
BASELINE_SPREAD_SCALE = 0.15
POOL_SHARE_OF_HOUR_ONE = 0.15
POOL_HOUR_ONE_FLOOR = 20_000.0
MIN_HOUR_POOL_SIZE = 5

DEFAULT_HOUR_ONE_VIEWERS = 1.0
MIN_OUTPUT_VALUE = 0.01
MAX_OUTPUT_VALUE = 1_000_000_000.0
SECONDS_PER_HOUR = 3600.0


def _parse_timestamp(value: Any) -> datetime | None:
    """Return an ISO-8601 timestamp, or ``None`` for a missing/invalid field."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _finite_float(value: Any) -> float | None:
    """Coerce a JSON number to a finite float without letting bad data escape."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _hourly_curve(comparable: Any) -> dict[int, float]:
    """Map hours 1-24 to viewer counts for one comparable launch.

    Missing day starts, null readings, malformed points, and holes in the hourly series
    are simply omitted.  A later valid reading for the same hour replaces an earlier one,
    matching the case-file order.
    """
    if not isinstance(comparable, Mapping):
        return {}

    day_start = _parse_timestamp(comparable.get("day_start"))
    if day_start is None:
        return {}

    points = comparable.get("hourly")
    if not isinstance(points, (list, tuple)):
        return {}

    curve: dict[int, float] = {}
    for point in points:
        if not isinstance(point, Mapping):
            continue

        viewers = _finite_float(point.get("viewers"))
        if viewers is None:
            continue

        observed_at = _parse_timestamp(point.get("from"))
        if observed_at is None:
            continue

        try:
            elapsed_hours = (observed_at - day_start).total_seconds() / SECONDS_PER_HOUR
            hour = int(round(elapsed_hours))
        except (TypeError, ValueError, OverflowError):
            continue

        if 1 <= hour <= 24:
            curve[hour] = viewers
    return curve


def _hour_one_viewers(case: Mapping[str, Any]) -> float:
    """Read the last valid hour-1 observation, defaulting safely when absent."""
    hour_one = DEFAULT_HOUR_ONE_VIEWERS
    known = case.get("known")
    if not isinstance(known, (list, tuple)):
        return hour_one

    for point in known:
        if not isinstance(point, Mapping) or point.get("hour") != 1:
            continue
        if point.get("viewers") is None:
            continue
        viewers = _finite_float(point["viewers"])
        hour_one = DEFAULT_HOUR_ONE_VIEWERS if viewers is None else viewers

    return max(hour_one, DEFAULT_HOUR_ONE_VIEWERS)


def _usable_curves(case: Mapping[str, Any]) -> list[dict[int, float]]:
    """Extract comparable curves that can observe hour 1 and at least one forecast hour."""
    raw_comparables = case.get("comparables")
    if not isinstance(raw_comparables, (list, tuple)):
        return []

    curves: list[dict[int, float]] = []
    for comparable in raw_comparables:
        curve = _hourly_curve(comparable)
        if curve.get(1) and any(hour in curve for hour in FORECAST_HOURS):
            curves.append(curve)
    return curves


def _hourly_log_ratios(
    curves: Sequence[Mapping[int, float]], case_hour_one: float
) -> dict[int, list[float]]:
    """Pool log(viewers_h / viewers_1) from comparables large enough to compare."""
    minimum_hour_one = max(
        POOL_HOUR_ONE_FLOOR,
        POOL_SHARE_OF_HOUR_ONE * case_hour_one,
    )
    pooled: dict[int, list[float]] = defaultdict(list)

    for curve in curves:
        hour_one = curve.get(1)
        if not hour_one or hour_one < minimum_hour_one:
            continue
        for hour in FORECAST_HOURS:
            viewers = curve.get(hour)
            if viewers and viewers > 0:
                pooled[hour].append(math.log(viewers / hour_one))
    return pooled


def _baseline_quantiles(hour_one: float, hour: int) -> list[float]:
    """House fallback used when an hour has too few comparable observations."""
    spread = BASELINE_SPREAD_SCALE * math.sqrt(hour - 1)
    return [hour_one * math.exp(z * spread) for z in QUANTILE_Z]


def _quantiles_for_hour(
    hour_one: float, hour: int, pooled: Mapping[int, Sequence[float]]
) -> list[float]:
    """Build five quantiles around a pooled median or the baseline fallback."""
    ratios = pooled.get(hour) or ()
    if len(ratios) < MIN_HOUR_POOL_SIZE:
        return _baseline_quantiles(hour_one, hour)

    centre = statistics.median(ratios)
    spread = SPREAD_SCALE * math.sqrt(hour - 1)
    return [hour_one * math.exp(centre + z * spread) for z in QUANTILE_Z]


def _finalize_quantiles(values: Sequence[float]) -> list[float]:
    """Round, bound, and monotonize a row while retaining finite positive values."""
    finalized: list[float] = []
    previous = 0.0
    for value in values:
        if not math.isfinite(value):
            value = 1.0
        value = min(max(round(value, 2), MIN_OUTPUT_VALUE), MAX_OUTPUT_VALUE)
        value = max(value, previous)
        finalized.append(value)
        previous = value
    return finalized


def make_forecast(case: Any) -> dict[str, list[dict[str, Any]]]:
    """Return the compact 23-row forecast object for one parsed case."""
    if not isinstance(case, Mapping):
        case = {}

    hour_one = _hour_one_viewers(case)
    pooled = _hourly_log_ratios(_usable_curves(case), hour_one)
    rows = [
        {
            "hour": hour,
            "q": _finalize_quantiles(_quantiles_for_hour(hour_one, hour, pooled)),
        }
        for hour in FORECAST_HOURS
    ]
    return {"hours": rows}


def main() -> int:
    """Forecast every ``*.in`` case in the input directory."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    case_paths = sorted(path for path in INPUT_DIR.glob("*.in") if path.is_file())
    if not case_paths:
        print(f"no case files in {INPUT_DIR}", file=sys.stderr)
        return 1

    for path in case_paths:
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = make_forecast(case)
        output_path = OUTPUT_DIR / f"{path.stem}.out"
        output_path.write_text(
            json.dumps(forecast, separators=(",", ":")),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
