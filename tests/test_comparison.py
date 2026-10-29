"""Controls for official reference comparisons and area aggregation."""
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from src.data.bplan import attach_locations
from src.data.areas import aggregate_area_period

class GeographyTests(unittest.TestCase):
    def test_five_digit_placeholder_is_excluded_from_bplan_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'bplan.csv'
            pd.DataFrame({'tiploc':['LGWITH'], 'location':['Langwith-Whaley Thorns'],
                'easting':[99999], 'northing':[99999]}).to_csv(path,index=False)
            out = attach_locations(pd.DataFrame({'station_name':['Langwith-Whaley Thorns']}), path, None).iloc[0]
            self.assertEqual(out.bplan_coordinate_status, 'invalid_or_missing')
            self.assertEqual(out.location_status, 'invalid_or_missing_coordinate')
            self.assertTrue(pd.isna(out.longitude))

    def test_official_coordinates_preserve_bplan_values(self):
        with tempfile.TemporaryDirectory() as folder:
            bplan, naptan = Path(folder) / "bplan.csv", Path(folder) / "naptan.csv"
            pd.DataFrame({"tiploc": ["COLESHL"], "location": ["Coleshill Parkway"], "easting": [111111], "northing": [222222]}).to_csv(bplan, index=False)
            pd.DataFrame({"ATCOCode": ["9100COLESHL"], "CommonName": ["Coleshill Parkway Rail Station"], "StopType": ["RLY"],
                          "Status": ["active"], "GridType": ["UKOS"], "Easting": [419900], "Northing": [291000]}).to_csv(naptan, index=False)
            out = attach_locations(pd.DataFrame({"station_name": ["Coleshill Parkway"]}), bplan, None, naptan).iloc[0]
            self.assertEqual(out.bplan_easting, 111111)
            self.assertEqual(out.easting, 419900)
            self.assertEqual(out.location_status, "official_station_reference")

    def test_area_rates_use_observed_counts(self):
        performance = pd.DataFrame({"station_name": ["A", "B"], "time_period": ["P1"] * 2,
                                    "period_start": ["2026-01-01"] * 2, "period_end": ["2026-01-28"] * 2, "period_days": [28] * 2,
                                    "scheduled_stops": [100, 900], "recorded_stops": [80, 800], "estimated_cancelled_calls": [20, 90],
                                    "cancellation_observed_scheduled_stops": [100, 900], "estimated_time_to_3_calls": [40, 720],
                                    "punctuality_observed_recorded_stops": [80, 800]})
        stations = pd.DataFrame({"station_name": ["A", "B"], "area_code": ["B"] * 2, "area_name": ["Bristol"] * 2})
        out = aggregate_area_period(performance, stations).iloc[0]
        self.assertEqual(out.cancellations_pct, 11)
        self.assertAlmostEqual(out.time_to_3_pct, 76000 / 880)

    def test_region_assignment_and_unallocated_stations_are_preserved(self):
        from src.data.areas import assign_areas
        import json
        import pyarrow as pa
        import pyarrow.parquet as pq
        from pyproj import CRS
        from shapely.geometry import box
        with tempfile.TemporaryDirectory() as folder:
            boundaries, areas = Path(folder) / 'boundaries.parquet', Path(folder) / 'areas.csv'
            table = pa.Table.from_pydict({'LAD24CD': ['A', 'B'], 'geometry': [box(0, 0, 10, 10).wkb, box(20, 0, 30, 10).wkb]})
            table = table.replace_schema_metadata({b'geo': json.dumps({'primary_column': 'geometry', 'columns': {'geometry': {'crs': CRS.from_epsg(27700).to_json_dict()}}}).encode()})
            pq.write_table(table, boundaries)
            pd.DataFrame({'area_code': ['A', 'B'], 'region_code': ['R', 'R'], 'area_name': ['Region', 'Region']}).to_csv(areas, index=False)
            stations = pd.DataFrame({'station_name': ['A station', 'B station', 'Unknown'], 'easting': [5, 25, 5],
                                     'northing': [5, 5, 5], 'location_status': ['official_station_reference', 'official_station_reference', 'unmatched']})
            out = assign_areas(stations, boundaries, areas)
            self.assertEqual(len(out), 3)
            self.assertEqual(out.area_code.dropna().tolist(), ['R', 'R'])
            self.assertEqual(out.member_code.dropna().tolist(), ['A', 'B'])
            self.assertTrue(pd.isna(out.area_code.iloc[2]))

    def test_exact_platform_group_validates_coordinates_without_changing_bplan_id(self):
        with tempfile.TemporaryDirectory() as folder:
            bplan, naptan = Path(folder) / 'bplan.csv', Path(folder) / 'naptan.csv'
            pd.DataFrame({'tiploc': ['MAIN'], 'location': ['Central'], 'easting': [0], 'northing': [0]}).to_csv(bplan, index=False)
            pd.DataFrame({'ATCOCode': ['9100PLAT1', '9100PLAT2'], 'CommonName': ['Central Rail Station'] * 2,
                          'StopType': ['RLY'] * 2, 'Status': ['active'] * 2, 'GridType': ['UKOS'] * 2,
                          'Easting': [400000, 400005], 'Northing': [300000, 300000]}).to_csv(naptan, index=False)
            out = attach_locations(pd.DataFrame({'station_name': ['Central']}), bplan, None, naptan).iloc[0]
            self.assertEqual(out.tiploc, 'MAIN')
            self.assertEqual(out.easting, 400000)
            self.assertEqual(out.bplan_easting, 0)
            self.assertEqual(out.location_status, 'official_station_reference')
