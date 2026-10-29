"""Controls for official-point validation and safe release creation."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import json
import pandas as pd

from src.data import validation


class ReleaseValidationTests(unittest.TestCase):
    def test_changed_authorities_export_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = pd.DataFrame({'station_name':['Changed','Same'],
                'naptan_lad_code':['A','A'], 'naptan_lad_name':['North & East','North & East'],
                'bplan_lad_code':['B','A'], 'bplan_lad_name':['South','North & East'],
                'bplan_distance_m':[42.1,0], 'lad_comparison_status':['changed','unchanged']})
            validation.write_changed_authorities(rows, root)
            exported = pd.read_csv(root/'bplan_changed_authorities.csv')
            self.assertEqual(exported.station_name.tolist(), ['Changed'])
            self.assertEqual(exported.naptan_lad_name.tolist(), ['North & East'])

    def test_coordinate_references_detect_boundary_crossings_without_snapping(self):
        with tempfile.TemporaryDirectory() as directory:
            boundary = Path(directory)/'lad.geojson'
            features = []
            for code, left in [('A', 0), ('B', 10)]:
                features.append({'type':'Feature', 'properties':{'LAD24CD':code},
                    'geometry':{'type':'Polygon','coordinates':[[[left,0],[left+10,0],[left+10,10],[left,10],[left,0]]]}})
            boundary.write_text(json.dumps({'type':'FeatureCollection',
                'crs':{'type':'name','properties':{'name':'EPSG:27700'}},'features':features}))
            rows = pd.DataFrame({'easting':[5,5,5,5,5], 'northing':[5]*5,
                'bplan_easting':[6,15,30,10,15], 'bplan_northing':[5]*5,
                'bplan_coordinate_status':['plausible_unvalidated']*4+['invalid_or_missing']})
            result = validation.compare_authorities(rows, boundary)
            self.assertEqual(result.lad_comparison_status.tolist(),
                ['unchanged','changed','unassigned_reference','unassigned_reference','unassigned_reference'])
            self.assertEqual(result.loc[1,'naptan_lad_code'], 'A')
            self.assertEqual(result.loc[1,'bplan_lad_code'], 'B')

    def test_official_validation_without_restricted_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pd.DataFrame({'station_name':['Example'], 'naptan_atco_code':['9100EXAMPLE'],
                'location_status':['official_station_reference'], 'mapping_method':['naptan_exact_name']}).to_csv(root/'station_locations.csv', index=False)
            result = validation.validate(root, root/'validation')
            self.assertEqual(result['official_points'], 1)
            self.assertNotIn('bplan_validation', result)
            self.assertFalse((root/'validation/bplan_validation.csv').exists())

    def test_duplicate_official_identifiers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pd.DataFrame({'station_name':['A','B'], 'naptan_atco_code':['9100A','9100A'],
                'location_status':['official_station_reference']*2}).to_csv(root/'station_locations.csv', index=False)
            with self.assertRaisesRegex(ValueError, 'identifiers'):
                validation.validate(root, root/'validation')

    def test_release_does_not_modify_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root/'existing.csv';sentinel.write_text('existing release')
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/'src/01_data.py'), '--root',str(root),
                '--orr',str(sentinel),'--calendar',str(sentinel),'--outdir',str(root)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('new output directory', result.stderr)
            self.assertEqual(sentinel.read_text(), 'existing release')
