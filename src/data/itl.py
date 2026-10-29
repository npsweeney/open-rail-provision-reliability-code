"""Extend completed ORR calculations with official 2025 ITL2/ITL3 geographies.

Station counts and rates are never averaged between levels. Each level is rebuilt
from station-period observations and pooled eligible station-day samples.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import numpy as np
import pandas as pd
from tqdm import tqdm
from pyproj import CRS, Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union
from shapely.prepared import prep
from shapely.validation import make_valid
from .areas import geographic_indices, orr_provision_per_head, pool_regional_periods, read_population


# Load full-resolution official ITL polygons and project them to metres
def read_boundaries(directory, level):
    path = directory / f'{level}_2025_bfe.geojson'
    data = json.loads(path.read_text())
    crs = data.get('crs', {}).get('properties', {}).get('name', 'EPSG:4326')
    project = Transformer.from_crs(crs, 27700, always_xy=True).transform
    code_field, name_field = level.upper() + '25CD', level.upper() + '25NM'
    rows, polygons = [], {}
    for feature in tqdm(data['features'], desc=f'Load {level.upper()} boundaries', unit='area', disable=None):
        props = feature['properties']; code = props[code_field]
        # TLN is Northern Ireland; the ORR release covers Great Britain
        if code.startswith('TLN'):
            continue
        if code in polygons:
            raise ValueError('Duplicate official boundary: ' + code)
        polygon = shape(feature['geometry'])
        if not CRS.from_user_input(crs).equals(CRS.from_epsg(27700)):
            polygon = transform(project, polygon)
        repaired = not polygon.is_valid
        if repaired:
            polygon = make_valid(polygon)
            if polygon.geom_type == 'GeometryCollection':
                polygon = unary_union([p for p in polygon.geoms if p.geom_type in ['Polygon', 'MultiPolygon']])
        if polygon.is_empty or not polygon.is_valid:
            raise ValueError('Invalid official ITL geometry: ' + code)
        polygons[code] = polygon
        rows.append({'area_code': code, 'area_name': props[name_field], 'geometry_repaired': repaired})
    return pd.DataFrame(rows), polygons


# Resolve each station's unique ITL path, including split LADs
def assign_itls(stations, lookup, catalogue, polygons, max_distance_m=250):
    if stations.station_name.duplicated().any() or not stations.location_status.eq('official_station_reference').all():
        raise ValueError('ITL assignment requires unique official station points.')
    columns = ['LAD24CD', 'ITL325CD', 'ITL325NM', 'ITL225CD', 'ITL225NM', 'ITL125CD', 'ITL125NM']
    members = lookup[columns].drop_duplicates()
    hierarchy = members.drop(columns='LAD24CD').drop_duplicates()
    if hierarchy.ITL325CD.duplicated().any():
        raise ValueError('ITL3 must have a unique ITL2 and ITL1 parent.')
    if set(hierarchy.ITL325CD) != set(catalogue.area_code):
        raise ValueError('Official lookup and GB boundary catalogues disagree.')
    candidates = members.groupby('LAD24CD').ITL325CD.agg(list).to_dict()
    prepared = {code: prep(polygon) for code, polygon in polygons.items()}
    rows = []
    for row in tqdm(stations.itertuples(), total=len(stations), desc='Assign station geography', unit='station', disable=None):
        codes = candidates.get(row.area_code, [])
        if not codes:
            raise ValueError('LAD absent from ITL lookup: ' + row.station_name)
        point = Point(row.easting, row.northing)
        covered = [code for code in codes if prepared[code].covers(point)]
        if len(covered) > 1:
            raise ValueError('Station lies on multiple ITL3 boundaries: ' + row.station_name)
        if covered:
            code, distance, method = covered[0], 0., 'inside_official_polygon'
        else:
            ranked = sorted((polygons[c].distance(point), c) for c in codes)
            distance, code = ranked[0]
            if distance > max_distance_m or (len(ranked) > 1 and abs(ranked[1][0] - distance) < 1e-6):
                raise ValueError(f'ITL boundary assignment needs review: {row.station_name}, {code}, {distance:.2f}m')
            method = 'nearest_official_boundary'
        rows.append({'station_name': row.station_name, 'lad_code': row.area_code, 'itl3_code': code,
                     'itl_assignment': method, 'itl_snap_distance_m': distance,
                     'split_lad': len(codes) > 1})
    result = pd.DataFrame(rows).merge(hierarchy.rename(columns={
        'ITL325CD':'itl3_code', 'ITL325NM':'itl3_name', 'ITL225CD':'itl2_code',
        'ITL225NM':'itl2_name', 'ITL125CD':'itl1_code', 'ITL125NM':'itl1_name'}),
        on='itl3_code', validate='many_to_one')
    if len(result) != len(stations):
        raise ValueError('Lost station during ITL assignment.')
    return result


# Read the matching-vintage Scottish ITL population table
def scottish_population(path, level):
    rows = pd.read_excel(path, sheet_name=level.upper(), header=3, usecols=range(5))
    rows = rows.loc[rows.Sex.eq('Persons') & rows.Year.eq(2024)].copy()
    code = next(c for c in rows if str(c).endswith('Code'))
    name = next(c for c in rows if str(c).endswith('Name'))
    rows = rows.rename(columns={code:'area_code', name:'area_name', 'All ages':'population_2024'})
    if rows.area_code.duplicated().any() or rows.population_2024.isna().any():
        raise ValueError('Invalid Scottish population table.')
    return rows[['area_code', 'area_name', 'population_2024']]


# Combine LAD totals and Scottish ITL estimates without duplicating islands
def itl_population(lookup, lad_population, scottish, catalogue, level):
    code, name = level.upper() + '25CD', level.upper() + '25NM'
    members = lookup[['LAD24CD', code, name]].drop_duplicates()
    ew = members.loc[members.LAD24CD.str.startswith(('E','W'))]
    if ew.LAD24CD.duplicated().any():
        raise ValueError('An English/Welsh LAD has multiple ITL memberships.')
    ew = ew.merge(lad_population, left_on='LAD24CD', right_on='area_code', validate='one_to_one')
    ew = ew.groupby([code, name], as_index=False).population_2024.sum().rename(columns={code:'area_code',name:'area_name'})
    total_scotland = lad_population.loc[lad_population.area_code.str.startswith('S'), 'population_2024'].sum()
    if scottish.population_2024.sum() != total_scotland:
        raise ValueError(f'Scottish population vintages disagree: {scottish.population_2024.sum()} vs {total_scotland}')
    result = pd.concat([ew[['area_code','population_2024']], scottish[['area_code','population_2024']]], ignore_index=True)
    result = catalogue[['area_code','area_name']].merge(result, on='area_code', how='left', validate='one_to_one')
    if result.population_2024.isna().any() or result.population_2024.le(0).any():
        raise ValueError('Incomplete ITL populations.')
    if result.population_2024.sum() != lad_population.population_2024.sum():
        raise ValueError('ITL populations do not reconcile to GB.')
    result['population_year'] = 2024
    return result


# Convert simplified polygons to plotting coordinates
def vertices(level, polygons):
    rows = []
    for code, geometry in polygons.items():
        geometry = geometry.simplify(50, preserve_topology=True)
        parts = list(geometry.geoms) if geometry.geom_type == 'MultiPolygon' else [geometry]
        for piece, polygon in enumerate(parts):
            for ring, line in enumerate([polygon.exterior, *polygon.interiors]):
                rows.extend((level, code, f'{code}_{piece}', f'{code}_{piece}_{ring}', x, y) for x,y in line.coords)
    return pd.DataFrame(rows, columns=['level','area_code','piece','ring','x','y'])


# Export one station path through LAD and ITL, with optional TTWA membership
def station_geographies(stations, assigned, ttwa=None):
    for frame in [stations, assigned] + ([ttwa] if ttwa is not None else []):
        if frame.station_name.isna().any() or frame.station_name.duplicated().any():
            raise ValueError('Geography links require unique populated station keys.')
        if set(frame.station_name) != set(stations.station_name):
            raise ValueError('Geography links must cover the same stations.')
    point_fields = ['station_name', 'naptan_atco_code', 'tiploc', 'longitude', 'latitude',
                    'easting', 'northing', 'coordinate_source', 'mapping_method',
                    'area_code', 'area_name', 'area_assignment']
    base = stations[point_fields].rename(columns={
        'area_code':'lad_code', 'area_name':'lad_name', 'area_assignment':'lad_assignment'})
    out = base.merge(assigned, on=['station_name','lad_code'], how='left',
                     validate='one_to_one', indicator='itl_join')
    if out.itl_join.ne('both').any():
        raise ValueError('Station LAD and ITL paths disagree.')
    out = out.drop(columns='itl_join')
    if ttwa is not None:
        names = {'area_code':'ttwa_code', 'area_name':'ttwa_name',
                 'area_assignment':'ttwa_assignment', 'snap_distance_m':'ttwa_snap_distance_m'}
        fields = ['station_name'] + [c for c in names if c in ttwa]
        out = out.merge(ttwa[fields].rename(columns=names), on='station_name', validate='one_to_one')
        if out.ttwa_code.isna().any() or out.ttwa_name.isna().any():
            raise ValueError('Incomplete TTWA membership in station links.')
    else:
        out['ttwa_code'], out['ttwa_name'] = pd.NA, pd.NA
    path = ['station_name','naptan_atco_code','lad_code','lad_name',
            'itl3_code','itl3_name','itl2_code','itl2_name','itl1_code','itl1_name',
            'ttwa_code','ttwa_name']
    if out[path[:10]].isna().any().any():
        raise ValueError('Incomplete administrative path in station links.')
    return out[path + [c for c in out if c not in path]].sort_values('station_name').reset_index(drop=True)


# Build nested ITL tables, populations and explicit station crosswalks
def build(source, outdir, raw, population_path, scotland_path, ttwa_dir=None, lookup_path=None):
    outdir.mkdir(parents=True, exist_ok=True)
    if source.resolve() != outdir.resolve():
        shutil.copytree(source, outdir, dirs_exist_ok=True)
    periods = pd.read_csv(outdir / 'station_period.csv')
    window = pd.read_csv(outdir / 'station_window.csv')
    stations = pd.read_csv(outdir / 'station_lad_assignments.csv')
    previous_indices = pd.read_csv(outdir / 'itl1_geographic_indices.csv')
    if previous_indices.fadi_quality_min.nunique() != 1 or window.minimum_index_periods.nunique() != 1:
        raise ValueError('Inconsistent index thresholds in source run.')
    quality_min = float(previous_indices.fadi_quality_min.iloc[0])
    minimum_periods = int(window.minimum_index_periods.iloc[0])
    lookup_path = lookup_path or raw / 'lad24_itl25_lookup.csv'
    lookup = pd.read_csv(lookup_path)
    lookup = lookup.loc[~lookup.LAD24CD.str.startswith('N')]
    boundary_dir = raw / 'itl_2025_official'
    catalogues, polygons = {}, {}
    for level in ['itl1','itl2','itl3']:
        catalogues[level], polygons[level] = read_boundaries(boundary_dir, level)
    assigned = assign_itls(stations, lookup, catalogues['itl3'], polygons['itl3'])
    assigned.to_csv(outdir / 'station_itl_assignments.csv', index=False)
    lookup.to_csv(outdir / 'lad_itl_hierarchy.csv', index=False)
    hierarchy = lookup[['ITL325CD','ITL325NM','ITL225CD','ITL225NM','ITL125CD','ITL125NM']].drop_duplicates()
    hierarchy.to_csv(outdir / 'itl_hierarchy.csv', index=False)
    ladpop = read_population(population_path)
    for level in ['itl2','itl3']:
        population = itl_population(lookup, ladpop, scottish_population(scotland_path, level), catalogues[level], level)
        population.to_csv(outdir / f'{level}_population_2024.csv', index=False)
        points = assigned[['station_name',level+'_code',level+'_name']].rename(columns={level+'_code':'area_code',level+'_name':'area_name'})
        rows = orr_provision_per_head(periods, points, population)
        pooled = pool_regional_periods(rows)
        rows.to_csv(outdir / f'{level}_orr_provision.csv', index=False)
        pooled.to_csv(outdir / f'{level}_orr_provision_window.csv', index=False)
        scores = geographic_indices(window, periods, points, pooled, quality_min, minimum_periods)
        scores.to_csv(outdir / f'{level}_geographic_indices.csv', index=False)
        # The authoritative total for incomplete source counts is the sum of known counts
        expected = periods.groupby('time_period').scheduled_stops.sum()
        calculated = rows.groupby('time_period').known_scheduled_calls.sum()
        if not np.allclose(calculated.reindex(expected.index), expected, rtol=0, atol=1e-6):
            raise ValueError('Scheduled calls not conserved at ' + level)
    old = pd.read_csv(outdir / 'map_vertices.csv')
    old = old.loc[~old.level.isin(['itl1','itl2','itl3','ttwa'])]
    displays = [old] + [vertices(level, polygons[level]) for level in ['itl1','itl2','itl3']]
    ttwa_stations = None
    if ttwa_dir:
        from .ttwa import read_ttwas
        areas, ttwa_polygons = read_ttwas(raw / 'ttwa_2011_uk_bsc_v3.zip')
        ttwa_polygons = {k:v for k,v in ttwa_polygons.items() if not k.startswith('N')}
        displays.append(vertices('ttwa', ttwa_polygons))
        ttwa_stations = pd.read_csv(ttwa_dir / 'station_ttwa_assignments.csv')
        for filename in ['ttwa_orr_provision.csv', 'ttwa_orr_provision_window.csv', 'ttwa_geographic_indices.csv']:
            shutil.copy2(ttwa_dir / filename, outdir / filename)
        ttwa_stations.to_csv(outdir / 'station_ttwa_assignments.csv', index=False)
    station_geographies(stations, assigned, ttwa_stations).to_csv(outdir / 'station_geographies.csv', index=False)
    pd.concat(displays, ignore_index=True).to_csv(outdir / 'map_vertices.csv', index=False)
    review = assigned.loc[assigned.split_lad | assigned.itl_snap_distance_m.gt(0)]
    review.to_csv(outdir / 'itl_station_review.csv', index=False)
    return assigned


# Add ITL geography to completed station CSVs
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--outdir',type=Path,required=True)
    parser.add_argument('--raw',type=Path,required=True);parser.add_argument('--population',type=Path,required=True)
    parser.add_argument('--scottish-population',type=Path,required=True);parser.add_argument('--ttwa',type=Path)
    a=parser.parse_args();build(a.source,a.outdir,a.raw,a.population,a.scottish_population,a.ttwa)

if __name__ == '__main__':main()
