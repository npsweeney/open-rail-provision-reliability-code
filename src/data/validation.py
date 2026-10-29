#!/usr/bin/env python3
"""Validate official station points; optionally compare an authorised BPLAN extract."""
import json
import numpy as np
import pandas as pd


def compare_authorities(result, boundaries):
    """Assign both coordinate references independently; leave outside points unassigned."""
    from pyproj import Transformer
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree
    from shapely.wkb import loads
    if boundaries.suffix == '.parquet':
        import pyarrow.parquet as pq
        table = pq.read_table(boundaries)
        meta = json.loads(table.schema.metadata[b'geo'])
        column = meta['primary_column']
        crs = meta['columns'][column]['crs']
        frame = table.to_pandas()
        codes = frame.LAD24CD.tolist()
        names = dict(zip(codes, frame.LAD24NM))
        polygons = [loads(value) for value in frame[column]]
    else:
        data = json.loads(boundaries.read_text())
        crs = data.get('crs', {}).get('properties', {}).get('name', 'EPSG:4326')
        codes = [f['properties']['LAD24CD'] for f in data['features']]
        names = {f['properties']['LAD24CD']: f['properties'].get('LAD24NM', f['properties']['LAD24CD']) for f in data['features']}
        polygons = [shape(f['geometry']) for f in data['features']]
    if len(set(codes)) != len(codes) or any(not p.is_valid for p in polygons):
        raise ValueError('Authority boundaries require unique codes and valid polygons.')
    tree = STRtree(polygons)
    transform = Transformer.from_crs(27700, crs, always_xy=True)
    out = result.copy()
    for prefix, east, north in [('naptan', 'easting', 'northing'),
                                ('bplan', 'bplan_easting', 'bplan_northing')]:
        assignments = []
        for row in out.itertuples():
            x, y = getattr(row, east), getattr(row, north)
            if not np.isfinite(x) or not np.isfinite(y) or (prefix == 'bplan' and row.bplan_coordinate_status != 'plausible_unvalidated'):
                assignments.append(pd.NA)
                continue
            point = Point(*transform.transform(x, y))
            matches = tree.query(point, predicate='intersects')
            assignments.append(codes[matches[0]] if len(matches) == 1 else pd.NA)
        out[prefix + '_lad_code'] = pd.array(assignments, dtype='string')
        out[prefix + '_lad_name'] = out[prefix + '_lad_code'].map(names)
    comparable = out.naptan_lad_code.notna() & out.bplan_lad_code.notna()
    changed = comparable & out.naptan_lad_code.ne(out.bplan_lad_code).fillna(False)
    out['lad_comparison_status'] = np.where(comparable, np.where(changed, 'changed', 'unchanged'), 'unassigned_reference')
    return out


def write_changed_authorities(result, out):
    """Write named authority differences as a supplementary CSV."""
    fields = ['station_name', 'naptan_lad_code', 'naptan_lad_name',
              'bplan_lad_code', 'bplan_lad_name', 'bplan_distance_m']
    changed = result.loc[result.lad_comparison_status.eq('changed'), fields].sort_values('station_name')
    changed.to_csv(out / 'bplan_changed_authorities.csv', index=False)



# Check official station identifiers and optionally compare BPLAN coordinates
def validate(source, out, bplan=None, mapping=None, lad_boundaries=None):
    if lad_boundaries and not bplan:
        raise ValueError('Authority comparison requires an authorised BPLAN reference.')
    stations = pd.read_csv(source / 'station_locations.csv')
    if stations.station_name.isna().any() or not stations.station_name.is_unique:
        raise ValueError('Station keys must be unique and populated.')
    if not stations.location_status.eq('official_station_reference').all():
        raise ValueError('Every station requires an official NaPTAN point.')
    if stations.naptan_atco_code.isna().any() or not stations.naptan_atco_code.is_unique:
        raise ValueError('NaPTAN identifiers must be unique and populated.')
    out.mkdir(parents=True, exist_ok=True)
    checks = {'stations': len(stations), 'official_points': int(stations.location_status.eq('official_station_reference').sum()),
              'mapping_methods': stations.mapping_method.value_counts().to_dict()}
    if bplan:
        from .bplan import attach_locations
        comparison = attach_locations(stations[['station_name']], bplan, mapping)
        fields = ['station_name', 'tiploc', 'bplan_easting', 'bplan_northing', 'bplan_coordinate_status']
        result = stations[['station_name', 'easting', 'northing', 'naptan_atco_code']].merge(
            comparison[fields], on='station_name', validate='one_to_one')
        valid = result.bplan_coordinate_status.eq('plausible_unvalidated')
        result['bplan_distance_m'] = np.hypot(result.easting - result.bplan_easting,
                                            result.northing - result.bplan_northing).where(valid)
        if lad_boundaries:
            result = compare_authorities(result, lad_boundaries)
            checks['bplan_authority_comparison'] = {
                'boundary_file': lad_boundaries.name,
                'counts': result.lad_comparison_status.value_counts().to_dict(),
                'method': 'Independent point-in-polygon assignments; outside or ambiguous points remain unassigned'}
            write_changed_authorities(result, out)
        result.to_csv(out / 'bplan_validation.csv', index=False)
        checks['bplan_validation'] = {'invalid_or_missing_points': int((~valid).sum()),
            'plausible_points': int(valid.sum()), 'median_distance_m': float(result.bplan_distance_m.median()),
            'over_1000m': int(result.bplan_distance_m.gt(1000).sum())}
    (out / 'summary.json').write_text(json.dumps(checks, indent=2) + '\n')
    return checks
