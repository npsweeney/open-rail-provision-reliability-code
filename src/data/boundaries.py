"""Cache official ONS January 2025 full-resolution ITL boundaries and provenance."""
import argparse
import gzip
import time
import json
from pathlib import Path
import urllib.parse
import urllib.request
from tqdm import tqdm

SERVICES = {
    'itl1': 'ITL1_JAN_2025_UK_BFE',
    'itl2': 'ITL2_JAN_2025_UK_BFE',
    'itl3': 'ITL3_JAN_2025_UK_BFE_V4',
}
BASE = 'https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/'
# This release matches the existing ONS mid-2024 population vintage
NRS_URL = 'https://www.nrscotland.gov.uk/media/b33atzkp/special-area-tables-mid-2022-to-mid-2024-11-dz.xlsx'


# Reuse a cached official file or download it with bounded retries
def fetch(url, path, refresh=False, progress=True):
    if path.exists() and not refresh:
        if progress:
            tqdm.write(f'Using cached {path.name}')
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            started = time.monotonic()
            if progress:
                print(f'{path.name}: waiting for server response and first data (attempt {attempt + 1}/3)', flush=True)
            request = urllib.request.Request(url, headers={'Accept-Encoding': 'gzip',
                'User-Agent': 'Mozilla/5.0 (Open Rail Provision and Reliability)'})
            with urllib.request.urlopen(request, timeout=300) as response:
                total = response.headers.get('Content-Length')
                read = getattr(response, 'read1', response.read)
                first = read(64 * 1024)
                chunks = [first]
                if progress:
                    print(f'{path.name}: receiving data after {time.monotonic() - started:.1f}s', flush=True)
                with tqdm(total=int(total) if total else None, initial=len(first), desc=path.name,
                          unit='B', unit_scale=True, disable=None if progress else True) as bar:
                    while chunk := read(64 * 1024):
                        chunks.append(chunk)
                        bar.update(len(chunk))
                content = b''.join(chunks)
                if response.headers.get('Content-Encoding') == 'gzip':
                    content = gzip.decompress(content)
            break
        except Exception as error:
            if attempt == 2:
                raise
            delay = max(60, int(error.headers.get('Retry-After', '60'))) if getattr(error, 'code', None) == 429 else 2
            tqdm.write(f'Retrying {path.name} in {delay}s: {error}')
            time.sleep(delay)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(content)
    temporary.replace(path)
    return path


# Download the official bulk export and retain its source metadata
def download_layer(endpoint, path):
    if path.exists():
        tqdm.write(f'Using cached {path.name}')
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(fetch(endpoint + '?f=pjson', path.with_suffix('.metadata.json'), progress=False).read_text())
    if metadata.get('error'):
        raise ValueError(metadata['error'])
    count_url = endpoint + '/query?where=1%3D1&returnCountOnly=true&f=json'
    expected = json.load(urllib.request.urlopen(count_url, timeout=60))['count']
    item = metadata['serviceItemId']
    url = f'https://open-geography-portalx-ons.hub.arcgis.com/api/download/v1/items/{item}/geojson?layers=0'
    pending = path.with_suffix('.download.geojson')
    for attempt in range(30):
        fetch(url, pending, refresh=True)
        data = json.loads(pending.read_text())
        if data.get('status') == 'Pending':
            pending.unlink()
            tqdm.write('ONS is preparing the bulk export; checking again in 10 seconds')
            for _ in tqdm(range(10), desc='Wait for bulk export', unit='s', disable=None):
                time.sleep(1)
            continue
        if data.get('error') or len(data.get('features', [])) != expected:
            pending.unlink()
            raise ValueError(f'Incomplete bulk boundary export: expected {expected} areas')
        pending.replace(path)
        print(path.name, expected, 'areas', flush=True)
        return path
    raise TimeoutError('ONS bulk export is still being prepared; rerun to resume')


# Download official ITL boundaries and matching Scottish populations
def build(raw):
    directory = raw / 'itl_2025_official'
    directory.mkdir(parents=True, exist_ok=True)
    for level, service in SERVICES.items():
        endpoint = BASE + service + '/FeatureServer/0'
        download_layer(endpoint, directory / f'{level}_2025_bfe.geojson')
    fetch(NRS_URL, raw / 'nrs_special_area_population_2022_2024_dz11.xlsx')


# Read the input directory and download the fixed geographic inputs
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    build(parser.parse_args().raw)


if __name__ == '__main__':
    main()
