"""Download the public inputs and prepare the geographic input formats."""
import json
import re
import urllib.parse
import urllib.request
from tqdm import tqdm
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.ops import transform, unary_union
from shapely.validation import make_valid
from .boundaries import BASE, fetch, download_layer, build as download_itls
from .population import build as build_ttwa_population

ORR_PAGE = 'https://dataportal.orr.gov.uk/performance'
NAPTAN_URL = 'https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv&atcoAreaCodes=910'
POPULATION_URL = 'https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/populationandmigration/populationestimates/datasets/populationestimatesforukenglandandwalesscotlandandnorthernireland/mid2024/mye24tablesuk.xlsx'
LAD_SERVICE = BASE + 'Local_Authority_Districts_December_2024_Boundaries_UK_BFE/FeatureServer/0'
LOOKUP_SERVICE = BASE + 'ITL125_ITL225_ITL325_LAU125_LAD24_UK_LU/FeatureServer/0'
TTWA_URL = 'https://hub.arcgis.com/api/v3/datasets/4d569ac4ad144a2d9e7c950bc920010c_0/downloads/data?format=shp&spatialRefId=27700&where=1%3D1'


# Find the complete Table 3130 workbook linked by ORR
def orr_url():
    request = urllib.request.Request(ORR_PAGE, headers={'User-Agent': 'Mozilla/5.0'})
    page = urllib.request.urlopen(request, timeout=60).read().decode()
    match = re.search(r'''href=["']([^"']*table-3130[^"']*\.ods)["']''', page)
    if not match:
        raise ValueError('ORR Table 3130 download link was not found.')
    return urllib.parse.urljoin(ORR_PAGE, match.group(1))


# Export the official LAD24-to-ITL25 lookup without collapsing split memberships
def download_lookup(path):
    if path.exists():
        tqdm.write(f'Using cached {path.name}')
        return
    tqdm.write(f'Downloading {path.name}')
    params = {'where': '1=1', 'outFields': '*', 'returnGeometry': 'false', 'f': 'json'}
    url = LOOKUP_SERVICE + '/query?' + urllib.parse.urlencode(params)
    data = json.load(urllib.request.urlopen(url, timeout=60))
    if data.get('error') or data.get('exceededTransferLimit'):
        raise ValueError('The official LAD-to-ITL lookup response is incomplete.')
    pd.DataFrame([f['attributes'] for f in data['features']]).to_csv(path, index=False)


# Convert full-resolution official LAD polygons to the GeoParquet used by processing
def prepare_lads(source, destination):
    if destination.exists():
        tqdm.write(f'Using prepared {destination.name}')
        return
    data = json.loads(source.read_text())
    crs = data.get('crs', {}).get('properties', {}).get('name', 'EPSG:4326')
    project = Transformer.from_crs(crs, 27700, always_xy=True).transform
    rows = []
    features = data['features']
    for feature in tqdm(features, desc='Prepare LAD GeoParquet', unit='area', disable=None):
        polygon = shape(feature['geometry'])
        if not CRS.from_user_input(crs).equals(CRS.from_epsg(27700)):
            polygon = transform(project, polygon)
        if not polygon.is_valid:
            polygon = make_valid(polygon)
            if polygon.geom_type == 'GeometryCollection':
                polygon = unary_union([g for g in polygon.geoms if g.geom_type in ['Polygon', 'MultiPolygon']])
        if polygon.is_empty or not polygon.is_valid:
            raise ValueError('Invalid LAD polygon: ' + feature['properties']['LAD24CD'])
        rows.append({'LAD24CD': feature['properties']['LAD24CD'],
                     'LAD24NM': feature['properties']['LAD24NM'], 'geometry': polygon.wkb})
    table = pa.Table.from_pandas(pd.DataFrame(rows), preserve_index=False)
    metadata = dict(table.schema.metadata or {})
    metadata[b'geo'] = json.dumps({'version': '1.0.0', 'primary_column': 'geometry',
        'columns': {'geometry': {'encoding': 'WKB', 'geometry_types': ['Polygon', 'MultiPolygon'],
                                'crs': CRS.from_epsg(27700).to_json_dict()}}}).encode()
    pq.write_table(table.replace_schema_metadata(metadata), destination)


# Download current rail observations and fixed-vintage geography and population inputs
def download_inputs(root, ttwa=False, refresh=False, workbook_url=None):
    raw = root/'data/raw'
    raw.mkdir(parents=True, exist_ok=True)
    workbook = raw/'table-3130-time-to-3-and-cancellations-by-station-and-operator.ods'
    if refresh or not workbook.exists():
        fetch(workbook_url or orr_url(), workbook, refresh=refresh)
    else:
        tqdm.write(f'Using cached {workbook.name}')
    fetch(NAPTAN_URL, raw/'naptan_rail.csv', refresh=refresh)
    print('ORR and NaPTAN ready', flush=True)
    fetch(POPULATION_URL, raw/'ons_mye2024_uk.xlsx')
    download_lookup(raw/'lad24_itl25_lookup.csv')
    lad_source = download_layer(LAD_SERVICE, raw/'lad_2024.geojson')
    prepare_lads(lad_source, raw/'lad_2024.parquet')
    download_itls(raw)
    if ttwa:
        fetch(TTWA_URL, raw/'ttwa_2011_uk_bsc_v3.zip')
        build_ttwa_population(root)
    print(f'Inputs: {raw}', flush=True)
