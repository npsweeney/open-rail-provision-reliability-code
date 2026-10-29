"""Controls for coastal TTWA assignment and pooled station-call weighting."""
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from shapely.geometry import box
from src.data.ttwa import assign_ttwas, summary_tables


class TtwaTests(unittest.TestCase):
    def stations(self,x):
        return pd.DataFrame({'station_name':['A'],'easting':[x],'northing':[5.],
                             'location_status':['official_station_reference']})

    def test_inside_and_limited_coastal_assignment(self):
        areas=pd.DataFrame({'area_code':['X'],'area_name':['Example']});polygons={'X':box(0,0,10,10)}
        inside=assign_ttwas(self.stations(5),areas,polygons)
        self.assertEqual(inside.area_assignment.iloc[0],'inside_polygon')
        near=assign_ttwas(self.stations(12),areas,polygons,max_snap_m=3)
        self.assertEqual(near.snap_distance_m.iloc[0],2)
        with self.assertRaisesRegex(ValueError,'needs review'):
            assign_ttwas(self.stations(12),areas,polygons,max_snap_m=1)

    def test_shared_border_and_wrong_pier_exception_fail(self):
        areas=pd.DataFrame({'area_code':['X','Y'],'area_name':['X','Y']})
        polygons={'X':box(0,0,10,10),'Y':box(10,0,20,10)}
        with self.assertRaisesRegex(ValueError,'Multiple covering'):
            assign_ttwas(self.stations(10),areas,polygons)
        rules=pd.DataFrame({'station_name':['A'],'area_code':['X'],'max_distance_m':[100.]})
        with self.assertRaisesRegex(ValueError,'exception fails'):
            assign_ttwas(self.stations(25),areas,polygons,overrides=rules)

    def test_reviewed_pier_uses_explicit_distance_limit(self):
        areas=pd.DataFrame({'area_code':['X'],'area_name':['Island']});polygons={'X':box(0,0,10,10)}
        rules=pd.DataFrame({'station_name':['A'],'area_code':['X'],'max_distance_m':[3.]})
        out=assign_ttwas(self.stations(12),areas,polygons,max_snap_m=1,overrides=rules)
        self.assertEqual(out.area_assignment.iloc[0],'reviewed_pier_boundary')
        rules['max_distance_m']=1
        with self.assertRaisesRegex(ValueError,'exception fails'):
            assign_ttwas(self.stations(12),areas,polygons,max_snap_m=1,overrides=rules)

    def test_summary_weights_events_and_keeps_empty_ttwa(self):
        window=pd.DataFrame({'station_name':['A','B'],'scheduled_stops':[100.,300.],'recorded_stops':[80.,240.],
            'estimated_cancelled_calls':[10.,60.],'cancellation_observed_scheduled_stops':[100.,300.],
            'estimated_time_to_3_calls':[40.,216.],'punctuality_observed_recorded_stops':[80.,240.],
            'period_start':['2026-01-01']*2,'period_end':['2026-01-12']*2,'period_days':[12]*2,
            'faci_eligible_days':[12]*2,'fadi_eligible_days':[12]*2,
            'exploratory_scheduled_stops':[100.,300.],'exploratory_recorded_stops':[80.,240.],
            'exploratory_cancellation_observed_scheduled_stops':[100.,300.],
            'exploratory_punctuality_observed_recorded_stops':[80.,240.],
            'exploratory_cancellations_pct':[10.,20.],'exploratory_delay_pct':[50.,10.]})
        periods=pd.DataFrame([{'station_name':name,'time_period':str(i),'period_days':1,
                              'faci_eligible':True,'fadi_eligible':True,'station_period_not_applicable':False}
                             for name in ['A','B'] for i in range(12)])
        stations=pd.DataFrame({'station_name':['A','B'],'area_code':['X','X']})
        areas=pd.DataFrame({'area_code':['X','Y'],'area_name':['Example','No stations']})
        window['minimum_index_periods'] = 12
        # Twelve contiguous observations retain unequal station call denominators.
        records = []
        for row in window.itertuples():
            for i in range(12):
                date = (pd.Timestamp('2026-01-01') + pd.Timedelta(days=i)).strftime('%Y-%m-%d')
                record = {'station_name':row.station_name, 'time_period':str(i), 'period_days':1,
                    'period_start':date, 'period_end':date, 'faci_eligible':True,
                    'fadi_eligible':True, 'station_period_not_applicable':False}
                for field in ['scheduled_stops','recorded_stops','estimated_cancelled_calls',
                    'cancellation_observed_scheduled_stops','estimated_time_to_3_calls','punctuality_observed_recorded_stops']:
                    record[field] = getattr(row, field) / 12
                records.append(record)
        periods = pd.DataFrame(records)
        stations['area_name'] = 'Example'
        population = areas.assign(population_2024=1000, population_year=2024)
        from src.data.areas import geographic_indices
        with patch('src.data.ttwa.geographic_indices', wraps=geographic_indices) as calculate_indices:
            rows, pooled, indices, summary = summary_tables(window,periods,stations,areas,population)
            self.assertEqual(calculate_indices.call_count, 1)
        self.assertEqual(len(rows), 24)
        self.assertEqual(len(pooled), 2)
        self.assertEqual(len(indices), 2)
        out = summary.set_index('area_code')
        self.assertEqual(out.loc['X','stations'],2)
        self.assertAlmostEqual(out.loc['X','cancellations_pct'],17.5)
        self.assertAlmostEqual(out.loc['X','delayed_pct'],20)
        self.assertAlmostEqual(out.loc['X','scheduled_calls_per_day'],400/12)
        self.assertAlmostEqual(out.loc['X','geographic_faci'],.175)
        self.assertAlmostEqual(out.loc['X','geographic_fadi'],.2)
        self.assertEqual(out.loc['Y','provision_status'],'no_orr_station')
        self.assertTrue(np.isnan(out.loc['Y','geographic_faci']))
