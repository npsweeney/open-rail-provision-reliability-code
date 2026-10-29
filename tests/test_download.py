"""Controls for public input downloads and geographic format preparation."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import pyarrow.parquet as pq
from pyproj import CRS
from shapely import from_wkb
from src.data.download import download_lookup, orr_url, prepare_lads
from src.data.boundaries import fetch, download_layer


class InputDownloadTests(unittest.TestCase):
    def test_workbook_link_comes_from_the_orr_page(self):
        page = b'<a href="/media/example/table-3130-stations.ods">Table 3130</a>'
        with patch('urllib.request.urlopen', return_value=io.BytesIO(page)):
            self.assertEqual(orr_url(), 'https://dataportal.orr.gov.uk/media/example/table-3130-stations.ods')

    def test_lookup_retains_split_lad_memberships(self):
        rows = [{'attributes': {'LAD24CD': 'S12000021', 'ITL325CD': code}} for code in ['TLM20', 'TLM93']]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'lookup.csv'
            with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps({'features': rows}).encode())):
                download_lookup(path)
            result = pd.read_csv(path)
            self.assertEqual(result.LAD24CD.tolist(), ['S12000021', 'S12000021'])
            self.assertEqual(set(result.ITL325CD), {'TLM20', 'TLM93'})

    def test_lookup_rejects_a_truncated_api_response(self):
        response = io.BytesIO(b'{"exceededTransferLimit": true, "features": []}')
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.urlopen', return_value=response):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                download_lookup(Path(folder)/'lookup.csv')

    def test_lad_conversion_retains_holes_and_declares_the_british_grid(self):
        rings = [[[-3, 52], [-2, 52], [-2, 53], [-3, 53], [-3, 52]],
                 [[-2.8, 52.2], [-2.8, 52.4], [-2.6, 52.4], [-2.6, 52.2], [-2.8, 52.2]]]
        data = {'features': [{'properties': {'LAD24CD': 'TEST', 'LAD24NM': 'Example'},
                              'geometry': {'type': 'Polygon', 'coordinates': rings}}]}
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/'lad.geojson', Path(folder)/'lad.parquet'
            source.write_text(json.dumps(data))
            prepare_lads(source, output)
            table = pq.read_table(output)
            metadata = json.loads(table.schema.metadata[b'geo'])
            self.assertTrue(CRS.from_user_input(metadata['columns']['geometry']['crs']).equals(CRS.from_epsg(27700)))
            polygon = from_wkb(table.to_pandas().geometry.iloc[0])
            self.assertEqual(len(polygon.interiors), 1)
            self.assertTrue(polygon.is_valid)

    def test_cached_inputs_are_reused_until_refresh_is_requested(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'source.csv'
            path.write_bytes(b'original')
            with patch('urllib.request.urlopen') as request:
                fetch('https://example.org/source.csv', path)
                request.assert_not_called()
            response = io.BytesIO(b'updated')
            response.headers = {}
            with patch('urllib.request.urlopen', return_value=response):
                fetch('https://example.org/source.csv', path, refresh=True)
            self.assertEqual(path.read_bytes(), b'updated')


class BoundaryDownloadTests(unittest.TestCase):
    def test_bulk_export_waits_until_ready_and_preserves_declared_crs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'lad.geojson'
            feature = {'properties': {'id': 1}, 'geometry': None}
            data = {'type': 'FeatureCollection', 'crs': {'type': 'name', 'properties': {'name': 'EPSG:27700'}}, 'features': [feature]}
            responses = iter([{'status': 'Pending'}, data])

            # Emulate the export being prepared before the complete file is served
            def download(url, destination, **kwargs):
                payload = next(responses) if destination.suffix == '.geojson' else {'serviceItemId': 'official-item'}
                destination.write_text(json.dumps(payload))
                return destination

            with patch('src.data.boundaries.fetch', side_effect=download) as request, \
                 patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"count":1}')), \
                 patch('src.data.boundaries.time.sleep') as sleep:
                download_layer('https://example.org/FeatureServer/0', path)
            self.assertEqual(json.loads(path.read_text()), data)
            self.assertEqual(sleep.call_count, 10)
            self.assertIn('/official-item/geojson?layers=0', request.call_args_list[-1].args[0])

    def test_incomplete_bulk_export_is_not_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'lad.geojson'

            # Emulate an export that omits a boundary
            def download(url, destination, **kwargs):
                payload = {'features': []} if destination.suffix == '.geojson' else {'serviceItemId': 'official-item'}
                destination.write_text(json.dumps(payload))
                return destination

            with patch('src.data.boundaries.fetch', side_effect=download), \
                 patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"count":1}')):
                with self.assertRaisesRegex(ValueError, 'Incomplete bulk'):
                    download_layer('https://example.org/FeatureServer/0', path)
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix('.download.geojson').exists())

    def test_completed_cache_does_not_request_another_export(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'lad.geojson'
            path.write_text('{"features":[]}')
            with patch('src.data.boundaries.fetch') as request:
                download_layer('https://example.org/FeatureServer/0', path)
            request.assert_not_called()

    def test_native_british_grid_coordinates_are_not_reprojected_as_degrees(self):
        from src.data.itl import read_boundaries
        ring = [[400000, 200000], [400100, 200000], [400100, 200100], [400000, 200100], [400000, 200000]]
        data = {'crs': {'type': 'name', 'properties': {'name': 'EPSG:27700'}},
                'features': [{'properties': {'LAD24CD': 'TEST', 'LAD24NM': 'Example', 'ITL125CD': 'TLC', 'ITL125NM': 'Example'},
                              'geometry': {'type': 'Polygon', 'coordinates': [ring]}}]}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'itl1_2025_bfe.geojson'
            source.write_text(json.dumps(data))
            output = Path(folder)/'lad.parquet'
            prepare_lads(source, output)
            polygon = from_wkb(pq.read_table(output).to_pandas().geometry.iloc[0])
            self.assertEqual(polygon.bounds, (400000, 200000, 400100, 200100))
            _, polygons = read_boundaries(Path(folder), 'itl1')
            self.assertEqual(polygons['TLC'].bounds, polygon.bounds)
