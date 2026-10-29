#!/usr/bin/env python3
"""Read ORR inputs, build station/area measures and write reproducible analysis CSVs."""
import shutil
import numpy as np
import pandas as pd

from .performance import (read_orr_sheet, prepare_operator_period, aggregate_station_period,
                         aggregate_station_window, enrich_period_fields, add_period_indices)


# Build station observations, geographic measures and table-ready CSVs
def build(orr, calendar, outdir, naptan=None, station_mapping=None,
          area_boundaries=None, population=None, itl_lookup=None, itl_boundaries=None,
          scottish_itl_population=None, ttwa_population=None, quality_min=80,
          sheet="3130b Stations and operators") -> int:
    from .stations import attach_naptan_locations
    if not 0 <= quality_min <= 100:
        raise ValueError("quality-min must lie between 0 and 100.")
    if station_mapping and not naptan:
        raise ValueError("station-mapping requires naptan.")
    if area_boundaries and not naptan:
        raise ValueError("Area comparison requires the official station coordinate reference.")
    if ttwa_population and not (naptan and area_boundaries):
        raise ValueError("TTWA measures require official station points and area boundaries.")
    if population and not area_boundaries:
        raise ValueError("Population comparison requires area boundaries.")
    if itl_lookup and not (area_boundaries and population):
        raise ValueError("ITL comparison requires boundaries and population.")
    print('Read ORR workbook', flush=True)
    raw = read_orr_sheet(orr, int(sheet) if sheet.isdigit() else sheet)
    print('Build station observations and indices', flush=True)
    rows = prepare_operator_period(raw, quality_min)
    standard = aggregate_station_period(rows)
    out = standard
    for measure, volume, rate in [("faci", "scheduled_stops", "cancellations_pct"), ("fadi", "recorded_stops", "delay_pct")]:
        eligible = aggregate_station_period(rows.loc[rows[measure + "_eligible"]])
        indices = add_period_indices(eligible)
        columns = ["time_period", "station_name", volume, rate, "reference_median_" + volume, measure, measure + "_100"]
        indices = indices[columns].rename(columns={c: "exploratory_" + c for c in columns[2:]})
        out = out.merge(indices, on=["time_period", "station_name"], how="left", validate="one_to_one")
    out["exploratory_fadi_quality_min"] = quality_min
    out = enrich_period_fields(out, calendar)
    if naptan:
        out = attach_naptan_locations(out, naptan, station_mapping)
    outdir.mkdir(parents=True, exist_ok=True)
    rows.to_csv(outdir / "operator_period.csv", index=False)
    station_window = aggregate_station_window(rows, out)
    for measure in ["faci", "fadi"]:
        supported = station_window.set_index("station_name")[measure + "_periods_sufficient"]
        insufficient = ~out.station_name.map(supported)
        out.loc[insufficient, ["exploratory_" + measure, "exploratory_" + measure + "_100"]] = np.nan
    station_window.to_csv(outdir / "station_window.csv", index=False)
    out.to_csv(outdir / "station_period.csv", index=False)
    dates = ["time_period", "period_start", "period_end", "period_days", "period_midpoint", "fiscal_year", "period"]
    out[dates].drop_duplicates().sort_values("period_start").to_csv(outdir / "time_periods.csv", index=False)
    if naptan:
        columns = ["station_name", "tiploc", "location", "easting", "northing", "longitude", "latitude", "location_status", "mapping_method",
                   "coordinate_source", "naptan_atco_code"]
        stations = out[columns].drop_duplicates()
        if area_boundaries:
            if itl_lookup:
                from .areas import administrative_provision, pool_regional_periods, geographic_indices
                lad, itl1, all_stations = administrative_provision(out, stations, area_boundaries, itl_lookup, population)
                lad.to_csv(outdir / "lad_orr_provision.csv", index=False)
                itl1.to_csv(outdir / "itl1_orr_provision.csv", index=False)
                pd.read_csv(itl_lookup)[["LAD24CD", "ITL125CD", "ITL125NM"]].drop_duplicates().to_csv(outdir / "lad_itl1_lookup.csv", index=False)
                lad_window, itl_window = pool_regional_periods(lad), pool_regional_periods(itl1)
                lad_window.to_csv(outdir / "lad_orr_provision_window.csv", index=False)
                itl_window.to_csv(outdir / "itl1_orr_provision_window.csv", index=False)
                geographic_indices(station_window, out, all_stations, lad_window, quality_min).to_csv(outdir / "lad_geographic_indices.csv", index=False)
                lookup = pd.read_csv(itl_lookup)[["LAD24CD", "ITL125CD"]].drop_duplicates()
                itl_stations = all_stations[["station_name", "area_code"]].merge(lookup, left_on="area_code", right_on="LAD24CD", how="left", validate="many_to_one")
                itl_stations = itl_stations.drop(columns="area_code").rename(columns={"ITL125CD": "area_code"})
                geographic_indices(station_window, out, itl_stations, itl_window, quality_min).to_csv(outdir / "itl1_geographic_indices.csv", index=False)
                all_stations.to_csv(outdir / "station_lad_assignments.csv", index=False)
                from .areas import map_vertices
                map_vertices(area_boundaries, itl_lookup).to_csv(outdir / "map_vertices.csv", index=False)
        stations.to_csv(outdir / "station_locations.csv", index=False)
    ttwa = None
    if ttwa_population:
        from .ttwa import build as build_ttwas
        ttwa = outdir / 'ttwa'
        ttwa.mkdir()
        shutil.copy2(ttwa_population, ttwa / 'ttwa_population_2024.csv')
        build_ttwas(outdir, ttwa, area_boundaries.parent / 'ttwa_2011_uk_bsc_v3.zip')
    if itl_boundaries:
        print('Build ITL geographies and indices', flush=True)
        if not (itl_lookup and population and scottish_itl_population):
            raise ValueError("ITL boundaries require lookup, ONS population and NRS ITL population.")
        from .itl import build as extend_itls
        extend_itls(outdir, outdir, itl_boundaries.parent, population,
                    scottish_itl_population, ttwa, itl_lookup)
    if itl_lookup:
        print('Prepare figure and table CSVs', flush=True)
        from .tables import build as prepare_figure_csvs
        prepare_figure_csvs(outdir, outdir / "figure_data")
    print(f"Wrote {len(out):,} station-period rows; {out.station_name.nunique():,} stations; {out.time_period.nunique():,} periods.")
    print(f"Scheduled calls preserved: {rows.scheduled_stops.sum():,.0f}")
    return 0
