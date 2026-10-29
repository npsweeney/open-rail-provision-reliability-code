#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calculate station performance and exploratory disruption indices from ORR Table 3130."""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd

# Header matching & small utils

# Standardise source column labels for matching
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())

# Find a required source column by its recognised label
def _pick_col(df: pd.DataFrame, target_norm: str) -> str:
    matches = [col for col in df.columns if re.sub(r"note\d+$", "", _norm(col)) == target_norm]
    if len(matches) != 1:
        raise KeyError(f"Expected one column {target_norm!r}; found {matches}. Available: {list(df.columns)}")
    return matches[0]

# Parse percentages while retaining unavailable values
def _to_pct(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    s = s.str.replace("%", "", regex=False).str.replace(",", "", regex=False)
    s = s.str.replace(r"^[\'`\u2019]+", "", regex=True)  # strip leading quotes/ticks
    return pd.to_numeric(s, errors="coerce")


# Read & aggregate (station × period)

# Read the selected worksheet directly from an ODS workbook
def _read_ods_sheet(workbook: Path, sheet: Union[str, int]) -> pd.DataFrame:
    table = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
    office = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
    text = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    active = False
    names = []
    rows = []
    with zipfile.ZipFile(workbook) as archive, archive.open("content.xml") as content:
        for event, element in ET.iterparse(content, events=("start", "end")):
            if event == "start" and element.tag == table + "table":
                name = element.get(table + "name", "")
                names.append(name)
                active = len(names) - 1 == sheet if isinstance(sheet, int) else _norm(name) == _norm(sheet)
            elif event == "end" and element.tag == table + "table-row":
                if active:
                    values = []
                    for cell in element:
                        if cell.tag not in (table + "table-cell", table + "covered-table-cell"):
                            continue
                        value = cell.get(office + "value")
                        if value is None:
                            value = " ".join("".join(p.itertext()) for p in cell.findall(text + "p")) or None
                        repeat = int(cell.get(table + "number-columns-repeated", "1"))
                        values.extend([value] * min(repeat, max(0, 12 - len(values))))
                    if any(value is not None for value in values):
                        repeat = int(element.get(table + "number-rows-repeated", "1"))
                        rows.extend([values] * repeat)
                element.clear()
            elif event == "end" and element.tag == table + "table":
                if active:
                    return pd.DataFrame(rows)
                element.clear()
    raise ValueError(f"Sheet {sheet!r} unavailable. Choose from {names}")


# Read the ORR station and operator worksheet
def read_orr_sheet(workbook: Union[str, Path], sheet: Union[str, int]) -> pd.DataFrame:
    workbook = Path(workbook)
    if workbook.suffix.lower() == ".ods":
        raw = _read_ods_sheet(workbook, sheet)
    else:
        with pd.ExcelFile(workbook) as book:
            if isinstance(sheet, str) and sheet not in book.sheet_names:
                matches = [name for name in book.sheet_names if _norm(name) == _norm(sheet)]
                if len(matches) != 1:
                    raise ValueError(f"Sheet {sheet!r} unavailable. Choose from {book.sheet_names}")
                sheet = matches[0]
            raw = pd.read_excel(book, sheet_name=sheet, header=None)
    mask = raw.iloc[:, 0].astype(str).str.strip().str.lower().eq("time period")
    if not mask.any():
        raise ValueError("Could not find the Time period header.")
    header = int(mask.idxmax())
    df = raw.iloc[header + 1:].copy()
    df.columns = raw.iloc[header].values
    return df.dropna(how="all").reset_index(drop=True)


# Parse source values and mark the separate FACI and FADI samples
def prepare_operator_period(df: pd.DataFrame, quality_min: float) -> pd.DataFrame:
    fields = {
        "time_period": "timeperiod", "station_name": "stationname",
        "operator": "operatorname", "scheduled_stops": "scheduledstops",
        "recorded_stops": "recordedstationstops",
        "recording_completeness_pct": "recordedstationstopspercentage",
        "time_to_3_pct": "timeto3percentage", "cancellations_pct": "cancellationspercentage",
    }
    out = pd.DataFrame({name: df[_pick_col(df, source)] for name, source in fields.items()})
    for name in ["time_period", "station_name", "operator"]:
        out[name] = out[name].astype("string").str.strip()
    identifiers = out[["time_period", "station_name", "operator"]]
    if identifiers.isna().any().any() or identifiers.eq("").any().any():
        raise ValueError("Missing station, operator or period identifier.")
    keys = ["time_period", "station_name", "operator"]
    if out.duplicated(keys).any():
        raise ValueError("Duplicate station/operator/period rows. Select Table 3130b only.")
    for name in [field for field in fields if field not in keys]:
        text = out[name].astype("string").str.strip()
        allowed_missing = text.isna() | text.isin(["[u]", "[z]", "", "nan", "null"])
        out[name + "_source"] = text
        values = _to_pct(text)
        if (values.isna() & ~allowed_missing).any():
            raise ValueError(f"Unrecognised numeric value in {name}.")
        if name.endswith("_pct") and (values.notna() & ~values.between(0, 100)).any():
            raise ValueError(f"Percentage outside 0–100 in {name}.")
        if name.endswith("_stops") and ((values < 0) | (values.notna() & (values % 1 != 0))).any():
            raise ValueError(f"Invalid count in {name}.")
        out[name] = values
    if (out["recorded_stops"] > out["scheduled_stops"]).any():
        raise ValueError("Recorded counts exceed scheduled counts.")
    out["faci_eligible"] = out.scheduled_stops.gt(0) & out.cancellations_pct.notna()
    out["fadi_eligible"] = (out.recorded_stops.gt(0) & out.time_to_3_pct.notna()
                           & out.recording_completeness_pct.ge(quality_min))
    return out


# Combine operators using each rate's observed call denominator
def aggregate_station_period(rows: pd.DataFrame) -> pd.DataFrame:
    tmp = rows.copy()
    c_known = tmp.cancellations_pct.notna()
    t_known = tmp.time_to_3_pct.notna() & tmp.recorded_stops.notna()
    tmp["cancellation_observed_scheduled_stops"] = tmp.scheduled_stops.where(c_known, 0)
    tmp["estimated_cancelled_calls"] = tmp.scheduled_stops * tmp.cancellations_pct / 100
    tmp["punctuality_observed_recorded_stops"] = tmp.recorded_stops.where(t_known, 0)
    tmp["estimated_time_to_3_calls"] = tmp.recorded_stops * tmp.time_to_3_pct / 100
    keys = ["time_period", "station_name"]
    columns = ["scheduled_stops", "recorded_stops", "cancellation_observed_scheduled_stops",
               "estimated_cancelled_calls", "punctuality_observed_recorded_stops", "estimated_time_to_3_calls"]
    agg = tmp.groupby(keys, as_index=False)[columns].sum(min_count=1)
    source_fields = [c + "_source" for c in ["scheduled_stops", "recorded_stops", "cancellations_pct", "time_to_3_pct"]]
    tmp["station_period_not_applicable"] = tmp[source_fields].eq("[z]").all(axis=1) if set(source_fields).issubset(tmp) else False
    agg["station_period_not_applicable"] = tmp.groupby(keys).station_period_not_applicable.all().to_numpy()
    for measure in ["faci", "fadi"]:
        if measure + "_eligible" in tmp:
            agg[measure + "_eligible"] = tmp.groupby(keys)[measure + "_eligible"].any().to_numpy()
    for count in ["scheduled_stops", "recorded_stops"]:
        missing = (tmp[count].isna() & ~tmp.station_period_not_applicable).groupby([tmp[k] for k in keys]).any()
        agg.loc[missing.to_numpy(), count] = np.nan
    agg["cancellations_pct"] = 100 * agg.estimated_cancelled_calls / agg.cancellation_observed_scheduled_stops.replace(0, np.nan)
    agg["time_to_3_pct"] = 100 * agg.estimated_time_to_3_calls / agg.punctuality_observed_recorded_stops.replace(0, np.nan)
    agg["delay_pct"] = 100 - agg.time_to_3_pct
    agg["cancellation_coverage_pct"] = 100 * agg.cancellation_observed_scheduled_stops / agg.scheduled_stops.replace(0, np.nan)
    agg["punctuality_coverage_pct"] = 100 * agg.punctuality_observed_recorded_stops / agg.recorded_stops.replace(0, np.nan)
    return agg


def required_index_periods(period_count: int) -> int:
    """Require at least one quarter of the observation window, rounded up."""
    if period_count < 1:
        raise ValueError('Index eligibility requires a non-empty reporting window.')
    return (period_count + 3) // 4


# Pool periods and calculate station frequency, coverage and indices
def aggregate_station_window(rows: pd.DataFrame, dates: pd.DataFrame, minimum_periods: int | None = None) -> pd.DataFrame:
    dates = dates[["time_period", "period_start", "period_end"]].drop_duplicates()
    if dates.time_period.duplicated().any():
        raise ValueError("Duplicate window calendar keys.")
    if minimum_periods is None:
        minimum_periods = required_index_periods(len(dates))
    dates = dates.sort_values("period_start").copy()
    start, end = pd.to_datetime(dates.period_start), pd.to_datetime(dates.period_end)
    if start.isna().any() or end.isna().any() or (end < start).any():
        raise ValueError("Invalid reporting dates in window calendar.")
    if not set(rows.time_period).issubset(set(dates.time_period)):
        raise ValueError("Window calendar is missing reporting periods from the input.")
    if (start.iloc[1:].to_numpy() != (end.iloc[:-1] + pd.Timedelta(days=1)).to_numpy()).any():
        raise ValueError("Window periods must be contiguous and non-overlapping.")
    days = int(((end - start).dt.days + 1).sum())
    fields = [c + "_source" for c in ["scheduled_stops", "recorded_stops", "cancellations_pct", "time_to_3_pct"]]
    prepared = rows.copy()
    not_applicable = prepared[fields].eq("[z]").all(axis=1)
    prepared.loc[not_applicable, ["scheduled_stops", "recorded_stops"]] = 0
    prepared["time_period"] = "All reporting periods"
    out = aggregate_station_period(prepared)
    out["period_start"], out["period_end"] = start.min().strftime("%Y-%m-%d"), end.max().strftime("%Y-%m-%d")
    out["period_days"] = days
    out["source_periods"] = out.station_name.map(rows.groupby("station_name").time_period.nunique())
    out["minimum_index_periods"] = minimum_periods
    day_lookup = pd.Series(((end - start).dt.days + 1).to_numpy(), index=dates.time_period)
    for measure, volume, rate, denominator, coverage in [
        ("faci", "scheduled_stops", "cancellations_pct", "cancellation_observed_scheduled_stops", "cancellation_coverage_pct"),
        ("fadi", "recorded_stops", "delay_pct", "punctuality_observed_recorded_stops", "punctuality_coverage_pct")]:
        subset = rows.loc[rows[measure + "_eligible"]]
        pooled = prepared.loc[prepared[measure + "_eligible"]]
        eligible = aggregate_station_period(pooled).set_index("station_name")
        for column in [volume, rate, denominator, coverage]:
            out["exploratory_" + column] = out.station_name.map(eligible[column])
        out[measure + "_eligible_periods"] = out.station_name.map(subset.groupby("station_name").time_period.nunique()).fillna(0).astype(int)
        eligible_dates = subset[["station_name", "time_period"]].drop_duplicates()
        eligible_dates["days"] = eligible_dates.time_period.map(day_lookup)
        out[measure + "_eligible_days"] = out.station_name.map(eligible_dates.groupby("station_name").days.sum()).fillna(0).astype(int)
        supported = out[measure + "_eligible_periods"].ge(minimum_periods)
        out[measure + "_periods_sufficient"] = supported
        out[measure + "_eligible_period_coverage_pct"] = 100 * out[measure + "_eligible_periods"] / len(dates)
        frequency = out["exploratory_" + volume] / out[measure + "_eligible_days"].replace(0, np.nan)
        median = frequency.loc[frequency.gt(0) & supported].median()
        out["exploratory_" + volume + "_per_eligible_day"] = frequency
        out["exploratory_reference_median_" + volume + "_per_day"] = median
        out["exploratory_" + measure] = out["exploratory_" + rate] / 100 * median / frequency.where(frequency.gt(0) & supported)
    out["period_coverage_pct"] = 100 * out.source_periods / len(dates)
    for volume in ["scheduled", "recorded"]:
        out[volume + "_calls_per_calendar_day"] = out[volume + "_stops"] / days
    return out


# Time-period parsing → period_midpoint (+ extras)
# (e.g. "Apr 2023 to Mar 2024 Period 01")

_TIME_RE = re.compile(
    r"([A-Za-z]{3})\s+(\d{4}).*?to.*?([A-Za-z]{3})\s+(\d{4}).*?Period\s+(\d{1,2})",
    re.IGNORECASE
)

# Join inclusive reporting dates and calculate calls per day
def enrich_period_fields(df: pd.DataFrame, calendar: Path) -> pd.DataFrame:
    dates = pd.read_csv(calendar, parse_dates=["period_start", "period_end"])
    if dates.time_period.duplicated().any():
        raise ValueError("Duplicate reporting periods in calendar.")
    if dates[["period_start", "period_end"]].isna().any().any() or (dates.period_end < dates.period_start).any():
        raise ValueError("Invalid reporting dates.")
    out = df.merge(dates, on="time_period", how="left", validate="many_to_one")
    if out.period_start.isna().any():
        missing = sorted(out.loc[out.period_start.isna(), 'time_period'].unique())
        raise ValueError(f"Calendar is missing reporting periods from the input. Add confirmed inclusive dates to {calendar}: {', '.join(missing)}")
    out["period_days"] = (out.period_end - out.period_start).dt.days + 1
    out["period_midpoint"] = out.period_start + (out.period_end - out.period_start) / 2
    parsed = out.time_period.str.extract(_TIME_RE)
    out["period"] = pd.to_numeric(parsed[4]).astype("Int64")
    out["fy_start_year"] = pd.to_numeric(parsed[1]).astype("Int64")
    out["fiscal_year"] = parsed[1] + "-" + parsed[3].str[-2:]
    out["scheduled_calls_per_calendar_day"] = out.scheduled_stops / out.period_days
    out["recorded_calls_per_calendar_day"] = out.recorded_stops / out.period_days
    return out


# Station-period indices use the eligible call-volume reference for the workbook
def add_period_indices(stations: pd.DataFrame) -> pd.DataFrame:
    out = stations.copy()
    for measure, volume, rate in [
        ("faci", "scheduled_stops", "cancellations_pct"),
        ("fadi", "recorded_stops", "delay_pct")]:
        # Scale each observed disruption share by inverse eligible call volume
        frequency = pd.to_numeric(out[volume], errors="coerce")
        percentage = pd.to_numeric(out[rate], errors="coerce")
        positive = frequency.gt(0)
        median = frequency.loc[positive].median()
        out["reference_median_" + volume] = median
        out[measure] = percentage.where(percentage.between(0, 100)) / 100 * median / frequency.where(positive)
        out[measure + "_100"] = out[measure] * 100
    return out
