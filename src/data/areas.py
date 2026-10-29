"""Assign station points to whole-authority regions and calculate provision per resident."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm
import pyarrow.parquet as pq
from pyproj import CRS
from shapely.geometry import Point
from shapely.wkb import loads


# Assign official station points to the supplied area polygons
def assign_areas(stations: pd.DataFrame, boundaries: Path, area_map: Path | pd.DataFrame) -> pd.DataFrame:
    areas = area_map.copy() if isinstance(area_map, pd.DataFrame) else pd.read_csv(area_map, dtype=str)
    if areas.area_code.isna().any() or areas.area_code.duplicated().any():
        raise ValueError("Duplicate comparison area codes.")
    table = pq.read_table(boundaries)
    metadata = json.loads(table.schema.metadata[b"geo"])
    geometry = metadata["primary_column"]
    crs = metadata["columns"][geometry]["crs"]
    if not CRS.from_user_input(crs).equals(CRS.from_epsg(27700)):
        raise ValueError("Authority boundaries must use British National Grid metres.")
    frame = table.to_pandas().set_index("LAD24CD")
    if not set(areas.area_code).issubset(frame.index):
        raise ValueError("Comparison area codes missing from the boundary file.")
    polygons = {row.area_code: loads(frame.loc[row.area_code, geometry]) for row in areas.itertuples()}
    if any(not polygon.is_valid for polygon in polygons.values()):
        raise ValueError("Invalid comparison boundary geometry.")
    out = stations.copy()
    out["area_code"] = pd.NA
    out["area_assignment"] = "coordinate_unavailable"
    for index, row in tqdm(out.iterrows(), total=len(out), desc='Assign stations to areas', unit='station', disable=None):
        if row.location_status != "official_station_reference":
            continue
        point = Point(row.easting, row.northing)
        matches = [code for code, polygon in polygons.items() if polygon.covers(point)]
        if len(matches) > 1:
            raise ValueError(f"Station falls on multiple comparison boundaries: {row.station_name}")
        out.loc[index, "area_assignment"] = "inside_comparison" if matches else "outside_comparison"
        if matches:
            out.loc[index, "area_code"] = matches[0]
    out = out.merge(areas, on="area_code", how="left", validate="many_to_one")
    if "region_code" in areas:
        if areas.region_code.isna().any() or areas.groupby("region_code").area_name.nunique().ne(1).any():
            raise ValueError("Each region needs one populated code and name.")
        out = out.rename(columns={"area_code": "member_code", "region_code": "area_code"})
    return out


# Pool station calls and observed rate denominators within each area
def aggregate_area_period(performance: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    if performance.duplicated(["station_name", "time_period"]).any():
        raise ValueError("Duplicate station-period keys in regional performance.")
    rows = performance.merge(stations[["station_name", "area_code", "area_name"]], on="station_name",
                             how="left", validate="many_to_one", indicator="station_join")
    if rows.station_join.ne("both").any():
        raise ValueError("Performance stations absent from the geography lookup.")
    selected = rows.loc[rows.area_code.notna()].copy()
    columns = ["scheduled_stops", "recorded_stops", "estimated_cancelled_calls", "cancellation_observed_scheduled_stops",
               "estimated_time_to_3_calls", "punctuality_observed_recorded_stops"]
    keys = ["area_code", "area_name", "time_period", "period_start", "period_end", "period_days"]
    out = selected.groupby(keys, as_index=False)[columns].sum(min_count=1)
    not_applicable = selected.get("station_period_not_applicable", pd.Series(False, index=selected.index))
    all_not_applicable = not_applicable.groupby([selected[k] for k in keys]).all()
    for column in ["scheduled_stops", "recorded_stops"]:
        missing = (selected[column].isna() & ~not_applicable).groupby([selected[k] for k in keys]).any()
        out.loc[all_not_applicable.to_numpy(), column] = 0
        out.loc[missing.to_numpy(), column] = np.nan
    out["cancellations_pct"] = 100 * out.estimated_cancelled_calls / out.cancellation_observed_scheduled_stops.replace(0, np.nan)
    out["time_to_3_pct"] = 100 * out.estimated_time_to_3_calls / out.punctuality_observed_recorded_stops.replace(0, np.nan)
    out["outside_time_to_3_pct"] = 100 - out.time_to_3_pct
    out["cancellation_coverage_pct"] = 100 * out.cancellation_observed_scheduled_stops / out.scheduled_stops.replace(0, np.nan)
    out["punctuality_coverage_pct"] = 100 * out.punctuality_observed_recorded_stops / out.recorded_stops.replace(0, np.nan)
    out["scheduled_calls_per_calendar_day"] = out.scheduled_stops / out.period_days
    return out


# Read positive mid-2024 LAD population estimates
def read_population(path: Path) -> pd.DataFrame:
    rows = pd.read_excel(path, sheet_name="MYE2 - Persons", header=7, usecols=range(4))
    rows = rows.rename(columns={"Code": "area_code", "Name": "population_area_name", "All ages": "population_2024"})
    rows = rows.loc[rows.area_code.str.match(r"^(E0[6789]|W06|S12)[0-9]{6}$", na=False)].copy()
    rows["population_2024"] = pd.to_numeric(rows.population_2024, errors="raise")
    if rows.area_code.duplicated().any() or rows.population_2024.isna().any() or rows.population_2024.le(0).any():
        raise ValueError("Population codes must be unique with positive observed populations.")
    return rows[["area_code", "population_area_name", "population_2024"]]


# Calculate area-period call provision, weighted rates and coverage
def orr_provision_per_head(performance: pd.DataFrame, stations: pd.DataFrame,
                          population: pd.DataFrame) -> pd.DataFrame:
    if population.area_code.isna().any() or population.area_code.duplicated().any():
        raise ValueError("Population area codes must be populated and unique.")
    if population.population_2024.isna().any() or population.population_2024.le(0).any():
        raise ValueError("Population denominators must be positive and observed.")
    if not set(stations.area_code.dropna()).issubset(set(population.area_code)):
        raise ValueError("Station areas absent from population denominators.")
    # Names must agree for each code before the area-period join
    expected_names = population.set_index("area_code").area_name
    selected_stations = stations.loc[stations.area_code.notna()]
    if population.area_name.isna().any() or selected_stations.area_name.isna().any() or selected_stations.area_name.ne(selected_stations.area_code.map(expected_names)).any():
        raise ValueError("Station area names disagree with population area names.")
    totals = aggregate_area_period(performance, stations)
    dates = performance[['time_period', 'period_start', 'period_end', 'period_days']].drop_duplicates()
    if dates.time_period.duplicated().any():
        raise ValueError("Inconsistent station-period reporting dates.")
    selected = stations.loc[stations.area_code.notna(), 'station_name']
    observed = performance.loc[performance.station_name.isin(selected)]
    counts = observed.groupby('station_name').time_period.nunique().reindex(selected, fill_value=0)
    if counts.ne(len(dates)).any():
        raise ValueError("Missing station-period rows in provision coverage; preserve explicit source missing codes.")
    grid = population.merge(dates, how='cross')
    keys = ['area_code', 'area_name', 'time_period', 'period_start', 'period_end', 'period_days']
    out = grid.merge(totals, on=keys, how='left', validate='one_to_one')
    detail = performance.merge(stations[['station_name', 'area_code']], on='station_name', validate='many_to_one')
    detail['station_period_not_applicable'] = detail.get('station_period_not_applicable', False)
    detail['missing_applicable_scheduled'] = detail.scheduled_stops.isna() & ~detail.station_period_not_applicable
    coverage = detail.groupby(['area_code', 'time_period'], as_index=False).agg(
        station_count=('station_name', 'size'),
        not_applicable_station_count=('station_period_not_applicable', 'sum'),
        numeric_scheduled_station_count=('scheduled_stops', 'count'),
        missing_scheduled_station_count=('missing_applicable_scheduled', 'sum'),
        known_scheduled_calls=('scheduled_stops', lambda x: x.sum(min_count=1)))
    out = out.merge(coverage, on=['area_code', 'time_period'], how='left', validate='one_to_one')
    no_stations = out.station_count.isna()
    out.loc[no_stations, ['scheduled_stops', 'recorded_stops', 'known_scheduled_calls',
                         'scheduled_calls_per_calendar_day', 'station_count',
                         'missing_scheduled_station_count', 'not_applicable_station_count',
                         'numeric_scheduled_station_count']] = 0
    out['applicable_station_count'] = out.station_count - out.not_applicable_station_count
    out['scheduled_station_coverage_pct'] = (100 * out.numeric_scheduled_station_count
                                             / out.applicable_station_count.replace(0, np.nan))
    out['listed_station_coverage_pct'] = (100 * out.numeric_scheduled_station_count
                                          / out.station_count.replace(0, np.nan))
    out.loc[out.applicable_station_count.eq(0), 'known_scheduled_calls'] = 0
    out['scheduled_calls_per_10000_residents'] = (
        10000 * out.scheduled_stops / out.period_days / out.population_2024)
    out['provision_status'] = np.select(
        [no_stations, out.applicable_station_count.eq(0), out.scheduled_stops.isna()],
        ['no_orr_station', 'no_applicable_station', 'missing_source_counts'], default='complete')
    out['population_year'] = 2024
    return out


# Pool area periods without averaging their rates
def pool_regional_periods(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.duplicated(['area_code', 'time_period']).any():
        raise ValueError('Duplicate area-period keys.')
    dates = rows[['time_period', 'period_start', 'period_end', 'period_days']].drop_duplicates().sort_values('period_start')
    if dates.time_period.duplicated().any():
        raise ValueError('Inconsistent regional reporting dates.')
    start, end = pd.to_datetime(dates.period_start), pd.to_datetime(dates.period_end)
    if ((end - start).dt.days + 1).ne(dates.period_days).any() or (start.iloc[1:].to_numpy() != (end.iloc[:-1] + pd.Timedelta(days=1)).to_numpy()).any():
        raise ValueError('Regional calendar must be contiguous with inclusive day counts.')
    if rows.groupby('area_code').size().ne(len(dates)).any():
        raise ValueError('Every area must cover the complete reporting window.')
    if rows.groupby('area_code')[['area_name', 'population_2024']].nunique(dropna=False).ne(1).any().any():
        raise ValueError('Regional names and population denominators must stay constant.')
    sums = ['scheduled_stops', 'recorded_stops', 'estimated_cancelled_calls', 'cancellation_observed_scheduled_stops',
            'estimated_time_to_3_calls', 'punctuality_observed_recorded_stops', 'station_count',
            'not_applicable_station_count', 'numeric_scheduled_station_count', 'missing_scheduled_station_count',
            'known_scheduled_calls', 'applicable_station_count']
    out = rows.groupby(['area_code', 'area_name', 'population_2024'], as_index=False)[sums].sum(min_count=1)
    for column in ['scheduled_stops', 'recorded_stops']:
        missing = rows.groupby('area_code')[column].apply(lambda x: x.isna().any())
        out.loc[out.area_code.map(missing), column] = np.nan
    out['time_period'] = 'All reporting periods'
    out['period_start'], out['period_end'] = start.min().strftime('%Y-%m-%d'), end.max().strftime('%Y-%m-%d')
    out['period_days'], out['reporting_periods'] = int(dates.period_days.sum()), len(dates)
    out['population_year'] = 2024
    out['cancellations_pct'] = 100 * out.estimated_cancelled_calls / out.cancellation_observed_scheduled_stops.replace(0, np.nan)
    out['time_to_3_pct'] = 100 * out.estimated_time_to_3_calls / out.punctuality_observed_recorded_stops.replace(0, np.nan)
    out['outside_time_to_3_pct'] = 100 - out.time_to_3_pct
    out['cancellation_coverage_pct'] = 100 * out.cancellation_observed_scheduled_stops / out.scheduled_stops.replace(0, np.nan)
    out['punctuality_coverage_pct'] = 100 * out.punctuality_observed_recorded_stops / out.recorded_stops.replace(0, np.nan)
    out['scheduled_station_coverage_pct'] = 100 * out.numeric_scheduled_station_count / out.applicable_station_count.replace(0, np.nan)
    out['listed_station_coverage_pct'] = 100 * out.numeric_scheduled_station_count / out.station_count.replace(0, np.nan)
    out['scheduled_calls_per_calendar_day'] = out.scheduled_stops / out.period_days
    out['scheduled_calls_per_10000_residents'] = 10000 * out.scheduled_calls_per_calendar_day / out.population_2024
    out['provision_status'] = np.select([out.station_count.eq(0), out.applicable_station_count.eq(0), out.scheduled_stops.isna()],
        ['no_orr_station', 'no_applicable_station', 'missing_source_counts'], default='complete')
    return out


# Build LAD and ITL1 measures from unique station assignments
def administrative_provision(performance: pd.DataFrame, stations: pd.DataFrame,
                             boundaries: Path, lookup: Path, population: Path):
    areas = pq.read_table(boundaries).to_pandas()[['LAD24CD', 'LAD24NM']]
    areas = areas.loc[~areas.LAD24CD.str.startswith('N')].copy()
    members = pd.read_csv(lookup, dtype=str)[['LAD24CD', 'ITL125CD', 'ITL125NM']].drop_duplicates()
    if members.LAD24CD.duplicated().any():
        raise ValueError('A LAD maps to multiple ITL1 regions.')
    areas = areas.merge(members, on='LAD24CD', how='left', validate='one_to_one')
    if areas.ITL125CD.isna().any():
        raise ValueError('Missing LAD to ITL1 memberships.')
    lad_map = areas.rename(columns={'LAD24CD': 'area_code', 'LAD24NM': 'area_name'})
    geography_columns = ['area_code', 'area_name', 'member_code', 'member_name', 'area_assignment']
    points = stations.drop(columns=[c for c in geography_columns if c in stations])
    located = assign_areas(points, boundaries, lad_map[['area_code', 'area_name']])
    overrides = pd.read_csv(Path(__file__).resolve().parents[2] / 'inputs/station_authority_overrides.csv')
    boundary_rows = pq.read_table(boundaries).to_pandas().set_index('LAD24CD')
    for rule in overrides.itertuples():
        selected = located.station_name.eq(rule.station_name)
        if not selected.any():
            continue
        point = located.loc[selected].iloc[0]
        polygon = loads(boundary_rows.loc[rule.area_code, 'geometry'])
        if point.location_status != 'official_station_reference' or polygon.distance(Point(point.easting, point.northing)) > rule.max_distance_m:
            raise ValueError('Reviewed coastal authority mapping fails distance check: ' + rule.station_name)
        located.loc[selected, 'area_code'] = rule.area_code
        located.loc[selected, 'area_name'] = boundary_rows.loc[rule.area_code, 'LAD24NM']
        located.loc[selected, 'area_assignment'] = 'reviewed_coastal_authority'
    pop = read_population(population)
    lad_population = lad_map[['area_code', 'area_name']].merge(pop.drop(columns='population_area_name'), on='area_code', validate='one_to_one')
    if len(lad_population) != len(lad_map):
        raise ValueError('Missing LAD population denominators.')
    lad = orr_provision_per_head(performance, located, lad_population)
    regional_stations = located.merge(lad_map[['area_code', 'ITL125CD', 'ITL125NM']], on='area_code', how='left', validate='many_to_one')
    regional_stations = regional_stations.drop(columns=['area_code', 'area_name']).rename(columns={'ITL125CD': 'area_code', 'ITL125NM': 'area_name'})
    region_pop = lad_population.merge(lad_map[['area_code', 'ITL125CD', 'ITL125NM']], on='area_code', validate='one_to_one')
    region_pop = region_pop.groupby(['ITL125CD', 'ITL125NM'], as_index=False).population_2024.sum().rename(columns={'ITL125CD': 'area_code', 'ITL125NM': 'area_name'})
    itl1 = orr_provision_per_head(performance, regional_stations, region_pop)
    return lad, itl1, located


# Simplify polygon outlines for figures, preserving holes
def map_vertices(boundaries: Path, lookup: Path) -> pd.DataFrame:
    from shapely.ops import unary_union
    geometry = pq.read_table(boundaries).to_pandas()
    geometry = geometry.loc[~geometry.LAD24CD.str.startswith('N')]
    members = pd.read_csv(lookup)[['LAD24CD', 'ITL125CD']].drop_duplicates()
    geometry = geometry.merge(members, on='LAD24CD', validate='one_to_one')
    shapes = [('lad', r.LAD24CD, loads(r.geometry)) for r in geometry.itertuples()]
    shapes += [('itl1', code, unary_union([loads(x) for x in group.geometry]))
               for code, group in geometry.groupby('ITL125CD')]
    records = []
    for level, code, shape in tqdm(shapes, desc='Prepare map outlines', unit='area', disable=None):
        shape = shape.simplify(50, preserve_topology=True)
        parts = list(shape.geoms) if shape.geom_type == 'MultiPolygon' else [shape]
        for piece, polygon in enumerate(parts):
            for ring, line in enumerate([polygon.exterior, *polygon.interiors]):
                records.extend((level, code, f'{code}_{piece}', f'{code}_{piece}_{ring}', x, y)
                               for x, y in line.coords)
    return pd.DataFrame(records, columns=['level', 'area_code', 'piece', 'ring', 'x', 'y'])


# Calculate area FACI and FADI from eligible calls per station-day
def geographic_indices(station_window: pd.DataFrame, station_period: pd.DataFrame,
                       stations: pd.DataFrame, areas: pd.DataFrame, quality_min: float = 80, minimum_periods: int | None = None) -> pd.DataFrame:
    if minimum_periods is None:
        from .performance import required_index_periods
        minimum_periods = required_index_periods(station_period.time_period.nunique())
    if station_window.station_name.duplicated().any() or stations.station_name.duplicated().any():
        raise ValueError('Geographic indices require unique station keys.')
    if station_period.duplicated(['station_name', 'time_period']).any() or areas.area_code.duplicated().any():
        raise ValueError('Duplicate station-period or area keys in geographic indices.')
    days = station_period.assign(applicable_days=station_period.period_days.where(
        ~station_period.station_period_not_applicable, 0)).groupby('station_name').applicable_days.sum()
    detail = station_window.merge(stations[['station_name', 'area_code']], on='station_name', how='left',
                                  validate='one_to_one', indicator='station_join')
    if detail.station_join.ne('both').any():
        raise ValueError('Index stations absent from the geography lookup.')
    if not set(stations.area_code.dropna()).issubset(set(areas.area_code)):
        raise ValueError('Index geography codes absent from area outputs.')
    detail['applicable_station_days'] = detail.station_name.map(days)
    if detail.applicable_station_days.isna().any():
        raise ValueError('Station window absent from station-period calendar.')
    out = areas[['area_code', 'area_name', 'scheduled_stops', 'recorded_stops', 'period_start', 'period_end', 'period_days']].copy()
    out['applicable_station_days'] = out.area_code.map(detail.groupby('area_code').applicable_station_days.sum())
    for measure, volume, rate, source_den, numerator in [
        ('faci', 'scheduled', 'cancellations_pct', 'cancellation_observed_scheduled_stops', 'index_estimated_cancelled_calls'),
        ('fadi', 'recorded', 'delay_pct', 'punctuality_observed_recorded_stops', 'index_estimated_delayed_calls')]:
        count_name = 'index_' + volume + '_calls'
        den_name = 'index_' + ('cancellation' if measure == 'faci' else 'delay') + '_denominator'
        days_name = measure + '_eligible_station_days'
        detail[count_name] = detail['exploratory_' + volume + '_stops']
        detail[den_name] = detail['exploratory_' + source_den]
        detail[numerator] = detail['exploratory_' + rate] * detail[den_name] / 100
        detail[days_name] = detail[measure + '_eligible_days']
        grouped = detail.groupby('area_code')[[count_name, den_name, numerator, days_name]].sum(min_count=1)
        missing = (detail[count_name].isna() & detail[days_name].gt(0)).groupby(detail.area_code).any()
        grouped.loc[missing, count_name] = np.nan
        for column in grouped:
            out[column] = out.area_code.map(grouped[column])
        no_sample = out[days_name].fillna(0).eq(0)
        out.loc[no_sample, [count_name, den_name, numerator, days_name]] = 0
        period_keys = station_period.loc[station_period[measure + '_eligible'], ['station_name', 'time_period']].merge(
            stations[['station_name', 'area_code']], on='station_name', how='left', validate='many_to_one')
        out[measure + '_eligible_periods'] = out.area_code.map(period_keys.groupby('area_code').time_period.nunique()).fillna(0).astype(int)
        supported = out[measure + '_eligible_periods'].ge(minimum_periods)
        out[measure + '_periods_sufficient'] = supported
        out[measure + '_station_day_coverage_pct'] = 100 * out[days_name] / out.applicable_station_days.replace(0, np.nan)
        rate_name = 'index_' + rate
        out[rate_name] = 100 * out[numerator] / out[den_name].replace(0, np.nan)
        frequency = out[count_name] / out[days_name].replace(0, np.nan)
        median = frequency.loc[frequency.gt(0) & supported].median()
        out['index_' + volume + '_calls_per_station_day'] = frequency
        out['reference_median_' + volume + '_calls_per_station_day'] = median
        out['reference_area_count_' + volume] = int((frequency.gt(0) & supported).sum())
        out['geographic_' + measure] = out[rate_name] / 100 * median / frequency.where(frequency.gt(0) & supported)
        out['geographic_' + measure + '_status'] = np.select([no_sample, ~supported, frequency.isna(), out[rate_name].isna()],
            ['no_eligible_station_days', 'insufficient_eligible_periods', 'missing_eligible_calls', 'missing_rate'], default='available')
        out['index_' + volume + '_call_coverage_pct'] = 100 * out[den_name] / out[count_name].replace(0, np.nan)
        out['eligible_' + volume + '_call_share_pct'] = 100 * out[count_name] / out[volume + '_stops'].replace(0, np.nan)
    out['fadi_quality_min'] = quality_min
    out['minimum_index_periods'] = minimum_periods
    return out
