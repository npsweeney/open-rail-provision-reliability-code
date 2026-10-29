"""Match stations directly to official NaPTAN rail points."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer


# Match ORR station names to official NaPTAN points and reviewed aliases
def attach_naptan_locations(performance: pd.DataFrame, naptan: Path, mapping: Path | None = None) -> pd.DataFrame:
    reference = pd.read_csv(naptan, dtype={"ATCOCode": str})
    reference = reference.loc[reference.StopType.eq("RLY") & reference.Status.eq("active")].copy()
    if reference.GridType.ne("UKOS").any():
        raise ValueError("NaPTAN station coordinates must use the UKOS grid.")
    reference["tiploc"] = reference.ATCOCode.str.extract(r"^9100([A-Z0-9]{1,7})$")[0]
    reference = reference.dropna(subset=["tiploc"])
    if reference.ATCOCode.duplicated().any() or reference.tiploc.duplicated().any():
        raise ValueError("Duplicate active NaPTAN rail identifiers.")
    for column in ["Easting", "Northing"]:
        reference[column] = pd.to_numeric(reference[column], errors="coerce")
    reference["name_key"] = reference.CommonName.str.replace(r" Rail Station$", "", regex=True).str.strip().str.casefold()
    spread = reference.groupby("name_key").agg(x=("Easting", lambda x: x.max() - x.min()), y=("Northing", lambda x: x.max() - x.min()))
    nearby = spread.index[np.hypot(spread.x, spread.y) <= 25]
    exact = reference.loc[reference.name_key.isin(nearby)].sort_values("ATCOCode").drop_duplicates("name_key").set_index("name_key")
    stations = performance[["station_name"]].drop_duplicates().copy()
    stations["name_key"] = stations.station_name.str.strip().str.casefold()
    stations["naptan_atco_code"] = stations.name_key.map(exact.ATCOCode).astype("string")
    stations["mapping_method"] = np.where(stations.naptan_atco_code.notna(), "naptan_exact_name", "unmatched")
    ambiguous = stations.name_key.isin(reference.name_key) & ~stations.name_key.isin(exact.index)
    stations.loc[ambiguous, "mapping_method"] = "ambiguous_name"
    if mapping:
        aliases = pd.read_csv(mapping, dtype=str)
        if {"station_name", "naptan_atco_code"} - set(aliases) or aliases.station_name.duplicated().any():
            raise ValueError("NaPTAN mapping requires unique station names and ATCO codes.")
        if aliases.naptan_atco_code.isna().any() or not aliases.naptan_atco_code.isin(reference.ATCOCode).all():
            raise ValueError("Reviewed alias is absent from active NaPTAN rail stations.")
        aliases = aliases.set_index("station_name")
        reviewed = stations.station_name.isin(aliases.index)
        stations.loc[reviewed, "naptan_atco_code"] = stations.loc[reviewed, "station_name"].map(aliases.naptan_atco_code)
        stations.loc[reviewed, "mapping_method"] = "reviewed_naptan_alias"
    stations = stations.merge(reference[["ATCOCode", "CommonName", "tiploc", "Easting", "Northing"]], left_on="naptan_atco_code", right_on="ATCOCode", how="left", validate="many_to_one")
    if stations.naptan_atco_code.dropna().duplicated().any():
        raise ValueError("Multiple ORR stations match the same NaPTAN rail point.")
    stations = stations.rename(columns={"CommonName": "location", "Easting": "easting", "Northing": "northing"})
    placeholders = [111111, 222222, 333333, 444444, 555555, 666666, 777777, 888888, 999999]
    valid = stations.easting.between(1, 700000) & stations.northing.between(1, 1300000)
    valid &= ~stations.easting.isin(placeholders) & ~stations.northing.isin(placeholders)
    stations["coordinate_source"] = stations.naptan_atco_code.map(lambda x: "NaPTAN" if pd.notna(x) else pd.NA)
    stations["location_status"] = stations.mapping_method.where(stations.naptan_atco_code.isna(), "invalid_or_missing_coordinate")
    stations.loc[valid, "location_status"] = "official_station_reference"
    stations["longitude"], stations["latitude"] = np.nan, np.nan
    transform = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
    lon, lat = transform.transform(stations.loc[valid, "easting"].tolist(), stations.loc[valid, "northing"].tolist())
    stations.loc[valid, "longitude"], stations.loc[valid, "latitude"] = lon, lat
    return performance.merge(stations.drop(columns=["name_key", "ATCOCode"]), on="station_name", how="left", validate="many_to_one")
