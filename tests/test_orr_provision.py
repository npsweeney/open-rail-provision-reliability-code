"""Controls for ORR per-head denominators, incomplete totals and zero stations."""
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from src.data.areas import orr_provision_per_head, pool_regional_periods


class OrrProvisionTests(unittest.TestCase):
    def test_missing_calls_are_distinct_from_no_station(self):
        performance = pd.DataFrame({
            'station_name': ['A', 'B', 'C'], 'time_period': ['P'] * 3,
            'period_start': ['2026-01-01'] * 3, 'period_end': ['2026-01-28'] * 3,
            'period_days': [28] * 3, 'scheduled_stops': [280, np.nan, 560],
            'recorded_stops': [250, np.nan, 500], 'estimated_cancelled_calls': [28, np.nan, 28],
            'cancellation_observed_scheduled_stops': [280, np.nan, 560],
            'estimated_time_to_3_calls': [225, np.nan, 400],
            'punctuality_observed_recorded_stops': [250, np.nan, 500]})
        stations = pd.DataFrame({'station_name': ['A', 'B', 'C'],
                                 'area_code': ['X', 'X', 'Y'], 'area_name': ['X', 'X', 'Y']})
        population = pd.DataFrame({'area_code': ['X', 'Y', 'Z'], 'area_name': ['X', 'Y', 'Z'],
                                   'population_2024': [10000, 20000, 5000]})
        out = orr_provision_per_head(performance, stations, population).set_index('area_code')
        self.assertTrue(pd.isna(out.loc['X', 'scheduled_calls_per_10000_residents']))
        self.assertEqual(out.loc['X', 'known_scheduled_calls'], 280)
        self.assertEqual(out.loc['X', 'scheduled_station_coverage_pct'], 50)
        self.assertEqual(out.loc['X', 'cancellations_pct'], 10)
        self.assertEqual(out.loc['Y', 'scheduled_calls_per_10000_residents'], 10)
        self.assertEqual(out.loc['Y', 'time_to_3_pct'], 80)
        self.assertEqual(out.loc['Z', 'scheduled_calls_per_10000_residents'], 0)
        self.assertEqual(out.loc['Z', 'provision_status'], 'no_orr_station')
        self.assertTrue(pd.isna(out.loc['Z', 'cancellations_pct']))

    def test_fully_not_applicable_station_is_outside_coverage_base(self):
        performance = pd.DataFrame({
            'station_name': ['A', 'B'], 'time_period': ['P'] * 2,
            'period_start': ['2026-01-01'] * 2, 'period_end': ['2026-01-28'] * 2,
            'period_days': [28] * 2, 'scheduled_stops': [280, np.nan],
            'recorded_stops': [250, np.nan], 'estimated_cancelled_calls': [28, np.nan],
            'cancellation_observed_scheduled_stops': [280, 0],
            'estimated_time_to_3_calls': [225, np.nan],
            'punctuality_observed_recorded_stops': [250, 0],
            'station_period_not_applicable': [False, True]})
        stations = pd.DataFrame({'station_name': ['A', 'B'], 'area_code': ['X', 'X'], 'area_name': ['X', 'X']})
        population = pd.DataFrame({'area_code': ['X'], 'area_name': ['X'], 'population_2024': [10000]})
        out = orr_provision_per_head(performance, stations, population).iloc[0]
        self.assertEqual(out.scheduled_calls_per_10000_residents, 10)
        self.assertEqual(out.scheduled_station_coverage_pct, 100)
        self.assertEqual(out.listed_station_coverage_pct, 50)
        self.assertEqual(out.not_applicable_station_count, 1)
        self.assertEqual(out.missing_scheduled_station_count, 0)
        self.assertEqual(out.cancellations_pct, 10)
        # Unknown applicable counts still make totals incomplete
        performance['station_period_not_applicable'] = False
        out = orr_provision_per_head(performance, stations, population).iloc[0]
        self.assertTrue(pd.isna(out.scheduled_calls_per_10000_residents))
        self.assertEqual(out.scheduled_station_coverage_pct, 50)


class PooledRegionTests(unittest.TestCase):
    def rows(self):
        return pd.DataFrame({
            'area_code': ['X', 'X'], 'area_name': ['Example', 'Example'], 'population_2024': [10000, 10000],
            'time_period': ['A', 'B'], 'period_start': ['2026-01-01', '2026-01-29'],
            'period_end': ['2026-01-28', '2026-03-04'], 'period_days': [28, 35],
            'scheduled_stops': [100, 900], 'recorded_stops': [80, 800],
            'estimated_cancelled_calls': [10, 180], 'cancellation_observed_scheduled_stops': [100, 900],
            'estimated_time_to_3_calls': [40, 720], 'punctuality_observed_recorded_stops': [80, 800],
            'station_count': [2, 2], 'not_applicable_station_count': [1, 0],
            'numeric_scheduled_station_count': [1, 2], 'missing_scheduled_station_count': [0, 0],
            'known_scheduled_calls': [100, 900], 'applicable_station_count': [1, 2]})

    def test_pool_uses_call_weights_calendar_days_and_station_period_coverage(self):
        out = pool_regional_periods(self.rows()).iloc[0]
        self.assertEqual(out.cancellations_pct, 19)
        self.assertAlmostEqual(out.outside_time_to_3_pct, 100 * 120 / 880)
        self.assertEqual(out.period_days, 63)
        self.assertAlmostEqual(out.scheduled_calls_per_10000_residents, 1000 / 63)
        self.assertEqual(out.scheduled_station_coverage_pct, 100)
        self.assertEqual(out.not_applicable_station_count, 1)

    def test_missing_period_total_remains_missing(self):
        rows = self.rows()
        rows.loc[0, 'scheduled_stops'] = np.nan
        out = pool_regional_periods(rows).iloc[0]
        self.assertTrue(pd.isna(out.scheduled_stops))
        self.assertTrue(pd.isna(out.scheduled_calls_per_10000_residents))
        self.assertEqual(out.known_scheduled_calls, 1000)

    def test_population_changes_rejected(self):
        rows = self.rows()
        rows.loc[0, 'population_2024'] = 12000
        with self.assertRaises(ValueError):
            pool_regional_periods(rows)


class GeographicIndexTests(unittest.TestCase):
    def inputs(self):
        window = pd.DataFrame({
            'station_name': ['A', 'B', 'C'], 'faci_eligible_days': [10, 30, 10], 'fadi_eligible_days': [10, 30, 10],
            'exploratory_scheduled_stops': [100., 900., 500.], 'exploratory_recorded_stops': [80., 800., 400.],
            'exploratory_cancellation_observed_scheduled_stops': [100., 900., 500.],
            'exploratory_punctuality_observed_recorded_stops': [80., 800., 400.],
            'exploratory_cancellations_pct': [10., 20., 10.], 'exploratory_delay_pct': [50., 10., 10.]})
        periods = pd.DataFrame({'station_name': ['A', 'B', 'C'], 'time_period': ['P'] * 3,
                                'period_days': [20, 30, 10], 'faci_eligible': [True]*3, 'fadi_eligible': [True]*3, 'station_period_not_applicable': [False] * 3})
        stations = pd.DataFrame({'station_name': ['A', 'B', 'C'], 'area_code': ['X', 'X', 'Y']})
        areas = pd.DataFrame({'area_code': ['X', 'Y', 'Z'], 'area_name': ['X', 'Y', 'Z'],
                             'scheduled_stops': [2000., 1000., 0.], 'recorded_stops': [1800., 800., 0.],
                             'period_start': ['2026-01-01'] * 3, 'period_end': ['2026-01-30'] * 3, 'period_days': [30] * 3})
        return window, periods, stations, areas

    def test_pooled_rates_station_day_frequency_and_level_medians(self):
        from src.data.areas import geographic_indices
        out = geographic_indices(*self.inputs(), minimum_periods=1).set_index('area_code')
        self.assertEqual(out.loc['X', 'index_cancellations_pct'], 19)
        self.assertAlmostEqual(out.loc['X', 'index_delay_pct'], 100 * 120 / 880)
        self.assertEqual(out.loc['X', 'index_scheduled_calls_per_station_day'], 25)
        self.assertEqual(out.loc['X', 'reference_median_scheduled_calls_per_station_day'], 37.5)
        self.assertAlmostEqual(out.loc['X', 'geographic_faci'], .19 * 37.5 / 25)
        self.assertEqual(out.loc['X', 'faci_station_day_coverage_pct'], 80)
        self.assertEqual(out.loc['X', 'eligible_scheduled_call_share_pct'], 50)
        self.assertTrue(pd.isna(out.loc['Z', 'geographic_faci']))

    def test_separate_station_day_denominators(self):
        from src.data.areas import geographic_indices
        window, periods, stations, areas = self.inputs()
        window['fadi_eligible_days'] = [5, 15, 10]
        out = geographic_indices(window, periods, stations, areas, minimum_periods=1).set_index('area_code')
        self.assertEqual(out.loc['X', 'index_scheduled_calls_per_station_day'], 25)
        self.assertEqual(out.loc['X', 'index_recorded_calls_per_station_day'], 44)
        self.assertEqual(out.loc['X', 'faci_station_day_coverage_pct'], 80)
        self.assertEqual(out.loc['X', 'fadi_station_day_coverage_pct'], 40)

    def test_area_scores_have_separate_quarter_period_minimums(self):
        from src.data.areas import geographic_indices
        window, periods, stations, areas = self.inputs()
        pieces = []
        for i in range(12):
            copy = periods.copy()
            copy['time_period'] = str(i)
            copy['fadi_eligible'] = i < 2
            pieces.append(copy)
        out = geographic_indices(window, pd.concat(pieces), stations, areas).set_index('area_code')
        self.assertEqual(out.loc['X', 'faci_eligible_periods'], 12)
        self.assertEqual(out.loc['X', 'minimum_index_periods'], 3)
        self.assertEqual(out.loc['X', 'fadi_eligible_periods'], 2)
        self.assertTrue(pd.notna(out.loc['X', 'geographic_faci']))
        self.assertTrue(pd.isna(out.loc['X', 'geographic_fadi']))

    def test_replicating_station_network_does_not_reward_area_size(self):
        from src.data.areas import geographic_indices
        window, periods, stations, areas = self.inputs()
        before = geographic_indices(window, periods, stations, areas, minimum_periods=1).set_index('area_code')
        copies = []
        for data in [window, periods, stations]:
            copy = data.loc[data.station_name.isin(['A', 'B'])].copy()
            copy['station_name'] += '_copy'
            copies.append(pd.concat([data, copy], ignore_index=True))
        after = geographic_indices(*copies, areas, minimum_periods=1).set_index('area_code')
        self.assertAlmostEqual(before.loc['X', 'geographic_faci'], after.loc['X', 'geographic_faci'])
        self.assertAlmostEqual(before.loc['X', 'geographic_fadi'], after.loc['X', 'geographic_fadi'])

    def test_missing_eligible_counts_remain_unavailable(self):
        from src.data.areas import geographic_indices
        window, periods, stations, areas = self.inputs()
        window.loc[0, 'exploratory_scheduled_stops'] = np.nan
        out = geographic_indices(window, periods, stations, areas, minimum_periods=1).set_index('area_code')
        self.assertTrue(pd.isna(out.loc['X', 'geographic_faci']))
        self.assertTrue(pd.isna(out.loc['X', 'index_scheduled_calls']))

    def test_unobserved_delay_keeps_its_denominator_separate(self):
        from src.data.areas import geographic_indices
        window, periods, stations, areas = self.inputs()
        window.loc[0, 'exploratory_delay_pct'] = np.nan
        window.loc[0, 'exploratory_punctuality_observed_recorded_stops'] = 0
        out = geographic_indices(window, periods, stations, areas, minimum_periods=1).set_index('area_code')
        self.assertAlmostEqual(out.loc['X', 'index_delay_pct'], 10)
        self.assertAlmostEqual(out.loc['X', 'index_recorded_call_coverage_pct'], 100 * 800 / 880)


class MissingGeographyTests(unittest.TestCase):
    def test_missing_station_lookup_cannot_drop_calls(self):
        from src.data.areas import aggregate_area_period
        performance = pd.DataFrame({'station_name': ['A', 'B'], 'time_period': ['P', 'P']})
        stations = pd.DataFrame({'station_name': ['A'], 'area_code': ['X'], 'area_name': ['X']})
        with self.assertRaisesRegex(ValueError, 'absent from the geography'):
            aggregate_area_period(performance, stations)

    def test_duplicate_station_period_cannot_double_counts(self):
        from src.data.areas import aggregate_area_period
        performance = pd.DataFrame({'station_name': ['A', 'A'], 'time_period': ['P', 'P']})
        stations = pd.DataFrame({'station_name': ['A'], 'area_code': ['X'], 'area_name': ['X']})
        with self.assertRaisesRegex(ValueError, 'Duplicate station-period'):
            aggregate_area_period(performance, stations)

    def test_missing_station_lookup_cannot_drop_index_sample(self):
        from src.data.areas import geographic_indices
        window, periods, stations, areas = GeographicIndexTests().inputs()
        with self.assertRaisesRegex(ValueError, 'absent from the geography'):
            geographic_indices(window, periods, stations.iloc[1:], areas, minimum_periods=1)

    def test_unknown_area_code_cannot_drop_index_sample(self):
        from src.data.areas import geographic_indices
        window, periods, stations, areas = GeographicIndexTests().inputs()
        stations.loc[0, 'area_code'] = 'UNKNOWN'
        with self.assertRaisesRegex(ValueError, 'absent from area outputs'):
            geographic_indices(window, periods, stations, areas, minimum_periods=1)

    def provision_inputs(self):
        rows = pd.DataFrame({'station_name': ['A', 'A', 'B'], 'time_period': ['P', 'Q', 'P'],
            'period_start': ['2026-01-01', '2026-01-29', '2026-01-01'],
            'period_end': ['2026-01-28', '2026-02-25', '2026-01-28'], 'period_days': [28] * 3,
            'scheduled_stops': [100.] * 3, 'recorded_stops': [90.] * 3,
            'estimated_cancelled_calls': [5.] * 3, 'cancellation_observed_scheduled_stops': [100.] * 3,
            'estimated_time_to_3_calls': [80.] * 3, 'punctuality_observed_recorded_stops': [90.] * 3})
        stations = pd.DataFrame({'station_name': ['A', 'B'], 'area_code': ['X', 'X'], 'area_name': ['X', 'X']})
        population = pd.DataFrame({'area_code': ['X'], 'area_name': ['X'], 'population_2024': [10000.]})
        return rows, stations, population

    def test_absent_station_period_cannot_claim_complete_coverage(self):
        with self.assertRaisesRegex(ValueError, 'Missing station-period rows'):
            orr_provision_per_head(*self.provision_inputs())

    def test_missing_population_code_cannot_drop_station_area(self):
        rows, stations, pop = self.provision_inputs()
        pop['area_code'] = 'OTHER'
        with self.assertRaisesRegex(ValueError, 'absent from population'):
            orr_provision_per_head(rows, stations, pop)

    def test_zero_population_is_rejected(self):
        rows, stations, pop = self.provision_inputs()
        pop['population_2024'] = 0
        with self.assertRaisesRegex(ValueError, 'positive and observed'):
            orr_provision_per_head(rows, stations, pop)

    def test_area_name_disagreement_is_rejected_before_join(self):
        rows, stations, pop = self.provision_inputs()
        stations['area_name'] = 'Different name for the same code'
        with self.assertRaisesRegex(ValueError, 'area names disagree'):
            orr_provision_per_head(rows, stations, pop)

    def test_duplicate_station_lookup_cannot_multiply_calls(self):
        rows, stations, pop = self.provision_inputs()
        stations = pd.concat([stations, stations.iloc[[0]]], ignore_index=True)
        with self.assertRaises(pd.errors.MergeError):
            orr_provision_per_head(rows, stations, pop)
