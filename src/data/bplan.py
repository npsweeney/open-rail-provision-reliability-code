"""Optional independent BPLAN comparison; never imported by the processing pipeline.

Network Rail, NWR BPLAN Geography v3.0 (2026), Rail Data Marketplace.
Access is governed by the university / Rail Data Marketplace
agreement. Supply an authorised local extract; no redistribution licence is granted.
"""
from __future__ import annotations
from pathlib import Path
import gzip
import numpy as np
import pandas as pd
from pyproj import Transformer


# Read the authorised BPLAN location reference for comparison
def read_bplan(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        locations = pd.read_csv(path, dtype=str)
        locations.columns = locations.columns.str.lower()
        locations = locations.rename(columns={"name": "location", "start_date": "active_from", "end_date": "active_to"})
    else:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8-sig") as source:
            records = [line.rstrip("\r\n").split("\t")[2:12] for line in source if line.startswith("LOC\t")]
        locations = pd.DataFrame(records, columns=["tiploc", "location", "active_from", "active_to", "easting", "northing", "timing_point_type", "zone", "stanox", "off_network"])
    required = ["tiploc", "location", "easting", "northing"]
    if set(required) - set(locations):
        raise ValueError(f"BPLAN requires {required}.")
    locations = locations[required].copy()
    if locations.tiploc.duplicated().any():
        raise ValueError("Duplicate TIPLOCs in the supplied BPLAN snapshot.")
    locations["name_key"] = locations.location.str.strip().str.casefold()
    for column in ["easting", "northing"]:
        locations[column] = pd.to_numeric(locations[column], errors="coerce")
    return locations


# Match BPLAN names and reviewed TIPLOCs for the independent comparison
def attach_locations(performance: pd.DataFrame, bplan: Path, mapping: Path | None, naptan: Path | None = None) -> pd.DataFrame:
    locations = read_bplan(bplan)
    stations = performance[["station_name"]].drop_duplicates().copy()
    stations["name_key"] = stations.station_name.str.strip().str.casefold()
    counts = locations.groupby("name_key").size()
    unique = locations.loc[locations.name_key.map(counts) == 1]
    stations = stations.merge(unique, on="name_key", how="left", validate="many_to_one")
    stations["mapping_method"] = np.where(stations.tiploc.notna(), "exact_name", "unmatched")
    ambiguous = stations.name_key.map(counts).fillna(0) > 1
    stations.loc[ambiguous, "mapping_method"] = "ambiguous_name"
    if mapping:
        reviewed = pd.read_csv(mapping, dtype=str)
        if set(["station_name", "tiploc"]) - set(reviewed) or reviewed.station_name.duplicated().any():
            raise ValueError("Station mapping needs unique station_name and tiploc columns.")
        reviewed = reviewed.merge(locations.drop(columns="name_key"), on="tiploc", how="left", validate="many_to_one")
        if reviewed.location.isna().any():
            raise ValueError("Reviewed mapping contains TIPLOCs absent from this BPLAN snapshot.")
        overrides = reviewed.set_index("station_name")
        mask = stations.station_name.isin(overrides.index)
        for column in ["tiploc", "location", "easting", "northing"]:
            stations.loc[mask, column] = stations.loc[mask, "station_name"].map(overrides[column])
        stations.loc[mask, "mapping_method"] = "reviewed_tiploc"
    stations["bplan_easting"] = stations.easting
    stations["bplan_northing"] = stations.northing
    bplan_valid = stations.bplan_easting.between(1, 700000) & stations.bplan_northing.between(1, 1300000)
    placeholders = [111111, 222222, 333333, 444444, 555555, 666666, 777777, 888888, 999999]
    bplan_valid &= ~stations.bplan_easting.isin(placeholders) & ~stations.bplan_northing.isin(placeholders)
    bplan_valid &= ~(stations.bplan_easting.eq(99999) & stations.bplan_northing.eq(99999))
    stations["bplan_coordinate_status"] = np.where(bplan_valid, "plausible_unvalidated", "invalid_or_missing")
    stations["coordinate_source"] = "BPLAN"
    stations["naptan_atco_code"] = pd.NA
    if naptan:
        reference = pd.read_csv(naptan, dtype={"ATCOCode": str})
        reference = reference.loc[reference.StopType.eq("RLY") & reference.Status.eq("active")].copy()
        if reference.GridType.ne("UKOS").any():
            raise ValueError("NaPTAN station coordinates must use the UKOS grid.")
        reference["tiploc"] = reference.ATCOCode.str.extract(r"^9100([A-Z0-9]{1,7})$")[0]
        reference = reference.dropna(subset=["tiploc"])
        if reference.tiploc.duplicated().any():
            raise ValueError("NaPTAN contains duplicate active station TIPLOCs.")
        reference["name_key"] = reference.CommonName.str.replace(r" Rail Station$", "", regex=True).str.strip().str.casefold()
        # Multiple platform TIPLOCs can describe the same physical station
        spread = reference.groupby("name_key").agg(x=("Easting", lambda x: x.max() - x.min()), y=("Northing", lambda x: x.max() - x.min()))
        nearby = spread.index[np.hypot(spread.x, spread.y) <= 25]
        exact_names = reference.loc[reference.name_key.isin(nearby)].sort_values("ATCOCode").drop_duplicates("name_key").set_index("name_key")
        recover = ~stations.tiploc.isin(reference.tiploc) & stations.name_key.isin(exact_names.index)
        if recover.any():
            for column, source in [("easting", "Easting"), ("northing", "Northing"), ("naptan_atco_code", "ATCOCode")]:
                stations.loc[recover, column] = stations.loc[recover, "name_key"].map(exact_names[source])
            missing_tiploc = recover & stations.tiploc.isna()
            stations.loc[missing_tiploc, "tiploc"] = stations.loc[missing_tiploc, "name_key"].map(exact_names.tiploc)
            stations.loc[recover, "mapping_method"] = "naptan_exact_station_group"
            stations.loc[recover, "coordinate_source"] = "NaPTAN"
        reference = reference.set_index("tiploc")
        matched = stations.tiploc.isin(reference.index)
        if matched.any():
            for column, source in [("easting", "Easting"), ("northing", "Northing"), ("naptan_atco_code", "ATCOCode")]:
                stations.loc[matched, column] = stations.loc[matched, "tiploc"].map(reference[source])
            stations.loc[matched, "coordinate_source"] = "NaPTAN"
    stations["coordinate_shift_m"] = np.hypot(stations.easting - stations.bplan_easting, stations.northing - stations.bplan_northing).where(bplan_valid & stations.coordinate_source.eq("NaPTAN"))
    plausible = stations.easting.between(1, 700000) & stations.northing.between(1, 1300000)
    placeholders = stations.easting.isin([111111, 222222, 333333, 444444, 555555, 666666, 777777, 888888, 999999]) | stations.northing.isin([111111, 222222, 333333, 444444, 555555, 666666, 777777, 888888, 999999])
    placeholders |= stations.easting.eq(99999) & stations.northing.eq(99999)
    valid = plausible & ~placeholders
    stations["location_status"] = "unmatched"
    stations.loc[ambiguous, "location_status"] = "ambiguous_name"
    stations.loc[stations.tiploc.notna(), "location_status"] = "invalid_or_missing_coordinate"
    stations.loc[valid, "location_status"] = "plausible_unvalidated"
    stations.loc[valid & stations.coordinate_source.eq("NaPTAN"), "location_status"] = "official_station_reference"
    stations["longitude"] = np.nan
    stations["latitude"] = np.nan
    transform = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
    lon, lat = transform.transform(stations.loc[valid, "easting"].tolist(), stations.loc[valid, "northing"].tolist())
    stations.loc[valid, "longitude"] = lon
    stations.loc[valid, "latitude"] = lat
    return performance.merge(stations.drop(columns="name_key"), on="station_name", how="left", validate="many_to_one")
