"""Assign official station points to ONS 2011 TTWAs and summarise pooled ORR measures."""
from __future__ import annotations
import argparse
import io
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from tqdm import tqdm
import shapefile
from pyproj import CRS
from shapely.geometry import Point, shape
from shapely.validation import make_valid, explain_validity
from shapely.ops import unary_union
from .areas import geographic_indices, orr_provision_per_head, pool_regional_periods


# Read official TTWA polygons and repair invalid polygon rings
def read_ttwas(path: Path):
    # Keep the original shapefile ZIP; read geometries in British National Grid metres
    with zipfile.ZipFile(path) as archive:
        stem = next(name[:-4] for name in archive.namelist() if name.lower().endswith('.shp'))
        if not CRS.from_wkt(archive.read(stem + '.prj').decode()).equals(CRS.from_epsg(27700)):
            raise ValueError('TTWA boundaries must use EPSG:27700.')
        reader = shapefile.Reader(**{ext: io.BytesIO(archive.read(stem + '.' + ext)) for ext in ['shp','shx','dbf']})
        records, polygons = [], {}
        for feature in reader.iterShapeRecords():
            record = feature.record.as_dict()
            code = record['TTWA11CD']
            if code in polygons:
                raise ValueError('Duplicate TTWA code: ' + code)
            polygon = shape(feature.shape.__geo_interface__)
            original_area = polygon.area
            reason = '' if polygon.is_valid else explain_validity(polygon)
            if reason:
                # Repair source topology and retain the polygonal parts for point assignment
                polygon = make_valid(polygon)
                if polygon.geom_type == 'GeometryCollection':
                    polygon = unary_union([part for part in polygon.geoms if part.geom_type in ['Polygon','MultiPolygon']])
            if polygon.is_empty or not polygon.is_valid:
                raise ValueError('Invalid TTWA geometry after repair: ' + code)
            polygons[code] = polygon
            records.append({'area_code': code, 'area_name': record['TTWA11NM'],
                            'geometry_repaired': bool(reason), 'geometry_issue': reason,
                            'repair_area_difference_m2': polygon.area-original_area})
    return pd.DataFrame(records), polygons


# Assign station points to TTWAs using bounded coastal exceptions
def assign_ttwas(stations, areas, polygons, max_snap_m=250, overrides=None):
    if stations.station_name.isna().any() or stations.station_name.duplicated().any():
        raise ValueError('Official station names must be populated and unique.')
    if not stations.location_status.eq('official_station_reference').all():
        raise ValueError('TTWA assignments require official station-reference points.')
    out = stations.drop(columns=[c for c in ['area_code','area_name','area_assignment','member_code','member_name'] if c in stations]).copy()
    if overrides is None:
        overrides = pd.DataFrame(columns=['station_name','area_code','max_distance_m'])
    if overrides.station_name.duplicated().any() or not set(overrides.area_code).issubset(polygons):
        raise ValueError('TTWA overrides require unique station names and known area codes.')
    rules = overrides.set_index('station_name')
    assignments = []
    for row in tqdm(out.itertuples(), total=len(out), desc='Assign stations to TTWAs', unit='station', disable=None):
        point = Point(row.easting, row.northing)
        covered = [code for code, polygon in polygons.items() if polygon.covers(point)]
        if len(covered) > 1:
            raise ValueError('Multiple covering TTWAs: ' + row.station_name)
        if covered:
            code, distance, method = covered[0], 0., 'inside_polygon'
        else:
            distances = sorted((polygon.distance(point), code) for code, polygon in polygons.items())
            distance, code = distances[0]
            if row.station_name in rules.index:
                rule = rules.loc[row.station_name]
                if code != rule.area_code or distance > rule.max_distance_m:
                    raise ValueError('Reviewed TTWA boundary exception fails: ' + row.station_name)
                method = 'reviewed_pier_boundary'
            else:
                if distance > max_snap_m or (len(distances) > 1 and abs(distances[1][0] - distance) < 1e-6):
                    raise ValueError(f'TTWA assignment needs review: {row.station_name}; nearest {code}, {distance:.2f}m')
                method = 'nearest_boundary'
        assignments.append({'station_name':row.station_name,'area_code':code,'area_assignment':method,
                            'snap_distance_m':distance,'boundary_distance_m':polygons[code].boundary.distance(point)})
    out = out.merge(pd.DataFrame(assignments), on='station_name', validate='one_to_one')
    out = out.merge(areas, on='area_code', how='left', validate='many_to_one')
    if out.area_name.isna().any():
        raise ValueError('Station TTWA code absent from boundary catalogue.')
    return out


# Calculate TTWA period, pooled and index tables once; derive plotting fields from them
def summary_tables(window, periods, stations, areas, population, quality_min=80):
    rows = orr_provision_per_head(periods, stations,
        population[['area_code', 'area_name', 'population_2024', 'population_year']])
    pooled = pool_regional_periods(rows)
    minimum_periods = int(window.minimum_index_periods.iloc[0])
    indices = geographic_indices(window, periods, stations, pooled, quality_min, minimum_periods)
    fields = ['scheduled_stops','recorded_stops','estimated_cancelled_calls',
              'cancellation_observed_scheduled_stops','estimated_time_to_3_calls','punctuality_observed_recorded_stops',
              'period_start','period_end','period_days','reporting_periods']
    out = areas.merge(pooled[['area_code'] + fields], on='area_code', validate='one_to_one')
    out['stations'] = out.area_code.map(stations.groupby('area_code').size()).fillna(0).astype(int)
    no_station = out.stations.eq(0)
    out.loc[no_station, ['scheduled_stops','recorded_stops','cancellation_observed_scheduled_stops',
                        'punctuality_observed_recorded_stops']] = 0
    values = pooled.set_index('area_code')
    for target, source in [('scheduled_calls_per_day','scheduled_calls_per_calendar_day'),
                           ('cancellations_pct','cancellations_pct'),
                           ('delayed_pct','outside_time_to_3_pct'),
                           ('cancellation_coverage_pct','cancellation_coverage_pct'),
                           ('delay_coverage_pct','punctuality_coverage_pct')]:
        out[target] = out.area_code.map(values[source])
    out['recorded_calls_per_day'] = out.recorded_stops / out.period_days
    out = out.merge(indices.drop(columns=['area_name','scheduled_stops','recorded_stops',
                    'period_start','period_end','period_days']), on='area_code', validate='one_to_one')
    out['scheduled_calls_per_applicable_station_day'] = out.scheduled_stops / out.applicable_station_days.replace(0,np.nan)
    out['provision_status'] = np.select([no_station,out.applicable_station_days.fillna(0).eq(0)],
        ['no_orr_station','no_applicable_station'],default='observed')
    return rows, pooled, indices, add_population(out, population)


# Join TTWA population and calculate daily provision per resident
def add_population(summary, population):
    if population.area_code.isna().any() or population.area_code.duplicated().any():
        raise ValueError('TTWA population codes must be unique.')
    if population.population_2024.isna().any() or (~np.isfinite(population.population_2024) | population.population_2024.le(0)).any():
        raise ValueError('TTWA populations must be positive and observed.')
    if not population.population_year.eq(2024).all():
        raise ValueError('The TTWA comparison uses 2024 population.')
    if set(summary.area_code) != set(population.area_code):
        raise ValueError('TTWA summary and population codes differ.')
    expected = population.set_index('area_code').area_name
    if summary.area_name.ne(summary.area_code.map(expected)).any():
        raise ValueError('TTWA names disagree between the summary and population lookup.')
    out = summary.merge(population.drop(columns='area_name'), on='area_code',how='left',validate='one_to_one')
    out['scheduled_calls_per_10000_residents_per_day'] = 10000 * out.scheduled_stops / out.period_days / out.population_2024
    return out


# Build TTWA assignments and summaries from the station CSVs
def build(source, outdir, boundaries, max_snap_m=250):
    path, run = boundaries, source
    outdir.mkdir(exist_ok=True, parents=True)
    areas, polygons = read_ttwas(path)
    # ORR and its station reference cover Great Britain; retain all UK boundaries in the raw ZIP
    areas.to_csv(outdir / 'boundary_catalogue.csv',index=False)
    areas = areas.loc[~areas.area_code.str.startswith('N')].copy()
    polygons = {code: polygons[code] for code in areas.area_code}
    vertices=[]
    for code, polygon in polygons.items():
        parts=list(polygon.geoms) if polygon.geom_type=='MultiPolygon' else [polygon]
        for piece, part in enumerate(parts):
            for ring, line in enumerate([part.exterior,*part.interiors]):
                vertices.extend((code,f'{code}_{piece}',f'{code}_{piece}_{ring}',x,y) for x,y in line.coords)
    pd.DataFrame(vertices,columns=['area_code','piece','ring','x','y']).to_csv(outdir / 'map_vertices.csv',index=False)

    official = pd.read_csv(run / 'station_locations.csv')
    overrides = pd.read_csv(Path(__file__).resolve().parents[2] / 'inputs/ttwa_station_overrides.csv')
    stations = assign_ttwas(official,areas,polygons,max_snap_m,overrides)
    stations.to_csv(outdir / 'station_ttwa_assignments.csv',index=False)
    stations.loc[stations.area_assignment.ne('inside_polygon') | stations.boundary_distance_m.le(200)].to_csv(outdir / 'boundary_review.csv',index=False)
    window = pd.read_csv(run / 'station_window.csv')
    periods = pd.read_csv(run / 'station_period.csv')
    quality = periods.exploratory_fadi_quality_min.drop_duplicates()
    if len(quality) != 1:
        raise ValueError('Inconsistent FADI quality threshold.')
    population = pd.read_csv(outdir / 'ttwa_population_2024.csv')
    rows, pooled, indices, result = summary_tables(window, periods, stations, areas, population, float(quality.iloc[0]))
    rows.to_csv(outdir / 'ttwa_orr_provision.csv', index=False)
    pooled.to_csv(outdir / 'ttwa_orr_provision_window.csv', index=False)
    indices.to_csv(outdir / 'ttwa_geographic_indices.csv', index=False)
    for field in ['scheduled_stops','recorded_stops','estimated_cancelled_calls','cancellation_observed_scheduled_stops',
                  'estimated_time_to_3_calls','punctuality_observed_recorded_stops']:
        np.testing.assert_allclose(result[field].sum(),window[field].sum(),rtol=1e-10,atol=1e-7)
    result.to_csv(outdir / 'ttwa_summary.csv',index=False)
    print(f'Wrote {len(result)} TTWA summaries.')


# Read input paths and build TTWA station summaries
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--boundaries', type=Path, required=True)
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.outdir, args.boundaries)


if __name__ == '__main__':
    main()
