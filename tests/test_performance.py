import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src.data.performance import aggregate_station_window, aggregate_station_period, prepare_operator_period, enrich_period_fields, add_period_indices
from src.data.bplan import attach_locations


def source_rows():
    return pd.DataFrame({
        "Time period": ["Apr 2023 to Mar 2024 Period 01"] * 2,
        "Station name": ["Test station"] * 2,
        "Operator name": ["A", "B"],
        "Scheduled stops": [100, 900],
        "Recorded station stops": [80, 800],
        "Recorded station stops (percentage)": [100, 100],
        "Time to 3 (percentage)": [50, 90],
        "Cancellations (percentage)": [20, 10],
    })


class PerformanceTests(unittest.TestCase):
    def test_source_columns_have_a_stable_order(self):
        rows = prepare_operator_period(source_rows(), 80)
        self.assertEqual([c for c in rows if c.endswith('_source')], [
            'scheduled_stops_source', 'recorded_stops_source', 'recording_completeness_pct_source',
            'time_to_3_pct_source', 'cancellations_pct_source'])

    def test_weighting_and_conservation(self):
        out = aggregate_station_period(prepare_operator_period(source_rows(), 80)).iloc[0]
        self.assertEqual(out.scheduled_stops, 1000)
        self.assertEqual(out.recorded_stops, 880)
        self.assertAlmostEqual(out.cancellations_pct, 11)
        self.assertAlmostEqual(out.time_to_3_pct, 100 * 760 / 880)
        self.assertEqual(out.cancellation_coverage_pct, 100)

    def test_missing_punctuality_keeps_volume_and_coverage(self):
        raw = source_rows().astype(object)
        raw.loc[1, "Time to 3 (percentage)"] = "[u]"
        out = aggregate_station_period(prepare_operator_period(raw, 80)).iloc[0]
        self.assertEqual(out.recorded_stops, 880)
        self.assertEqual(out.time_to_3_pct, 50)
        self.assertAlmostEqual(out.punctuality_coverage_pct, 100 * 80 / 880)

    def test_missing_cancellation_preserves_scheduled_denominator(self):
        raw = source_rows().astype(object)
        raw.loc[1, "Cancellations (percentage)"] = "[u]"
        out = aggregate_station_period(prepare_operator_period(raw, 80)).iloc[0]
        self.assertEqual(out.scheduled_stops, 1000)
        self.assertEqual(out.cancellations_pct, 20)
        self.assertEqual(out.cancellation_coverage_pct, 10)

    def test_all_missing_rates_remain_missing(self):
        raw = source_rows().astype(object)
        raw["Time to 3 (percentage)"] = "[u]"
        out = aggregate_station_period(prepare_operator_period(raw, 80)).iloc[0]
        self.assertTrue(np.isnan(out.time_to_3_pct))
        self.assertTrue(np.isnan(out.delay_pct))

    def test_duplicates_rejected_and_commas_parsed(self):
        raw = source_rows().astype(object)
        raw.loc[0, "Scheduled stops"] = "1,000"
        self.assertEqual(prepare_operator_period(raw, 80).scheduled_stops.iloc[0], 1000)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            prepare_operator_period(pd.concat([raw, raw]), 80)

    def test_missing_punctuality_codes_share_the_same_index_eligibility(self):
        for code in ["[u]", "[z]", "null", "", None]:
            raw = source_rows().astype(object)
            raw["Time to 3 (percentage)"] = code
            rows = prepare_operator_period(raw, 80)
            self.assertTrue(rows.faci_eligible.all(), code)
            out = aggregate_station_period(rows).iloc[0]
            self.assertTrue(pd.isna(out.delay_pct))
            self.assertEqual(out.cancellations_pct, 11)

    def test_separate_faci_fadi_eligibility(self):
        raw = source_rows().astype(object)
        raw.loc[0, 'Recorded station stops (percentage)'] = 39.7
        raw.loc[0, 'Time to 3 (percentage)'] = '[u]'
        raw.loc[1, 'Cancellations (percentage)'] = '[u]'
        out = prepare_operator_period(raw, 80)
        self.assertTrue(out.faci_eligible.iloc[0])
        self.assertFalse(out.fadi_eligible.iloc[0])
        self.assertFalse(out.faci_eligible.iloc[1])
        self.assertTrue(out.fadi_eligible.iloc[1])

    def test_zero_recorded_volume_gives_missing_punctuality(self):
        raw = source_rows()
        raw["Recorded station stops"] = 0
        out = aggregate_station_period(prepare_operator_period(raw, 80)).iloc[0]
        self.assertTrue(np.isnan(out.time_to_3_pct))

    def test_not_applicable_station_remains_unavailable(self):
        raw = source_rows().iloc[[0]].astype(object)
        raw["Operator name"] = "[z]"
        for column in raw.columns[3:]:
            raw[column] = "[z]"
        prepared = prepare_operator_period(raw, 80)
        self.assertFalse(prepared.faci_eligible.iloc[0])
        out = aggregate_station_period(prepared).iloc[0]
        self.assertTrue(np.isnan(out.scheduled_stops))
        self.assertTrue(np.isnan(out.recorded_stops))
        self.assertTrue(np.isnan(out.cancellations_pct))

    def test_calendar_includes_financial_year_boundary_days(self):
        root = Path(__file__).resolve().parents[1]
        rows = aggregate_station_period(prepare_operator_period(source_rows(), 80))
        out = enrich_period_fields(rows, root / "inputs/reporting_periods.csv").iloc[0]
        self.assertEqual(out.period_days, 29)
        self.assertEqual(str(out.period_end.date()), "2023-04-29")
        self.assertAlmostEqual(out.scheduled_calls_per_calendar_day, 1000 / 29)
        calendar = pd.read_csv(root / "inputs/reporting_periods.csv", parse_dates=["period_start", "period_end"])
        days = (calendar.period_end - calendar.period_start).dt.days + 1
        self.assertEqual(list(days.groupby(calendar.time_period.str[4:8]).sum()), [366, 365, 365, 365])
        p4 = calendar.loc[calendar.time_period.eq("Apr 2026 to Mar 2027 Period 04")].iloc[0]
        self.assertEqual(str(p4.period_start.date()), "2026-06-28")
        self.assertEqual(str(p4.period_end.date()), "2026-07-25")

    def test_empty_exploratory_subset_preserves_standard_result(self):
        raw = source_rows()
        raw["Recorded station stops (percentage)"] = 40
        rows = prepare_operator_period(raw, 80)
        indices = add_period_indices(aggregate_station_period(rows.loc[rows.fadi_eligible]))
        self.assertTrue(indices.empty)
        self.assertIn("faci", indices)
        self.assertEqual(aggregate_station_period(rows).scheduled_stops.sum(), 1000)

    def test_period_indices_scale_by_eligible_call_volume(self):
        rows = pd.DataFrame({"scheduled_stops": [100, 300], "recorded_stops": [80, 240],
                             "cancellations_pct": [10, 10], "delay_pct": [20, 20]})
        out = add_period_indices(rows)
        np.testing.assert_allclose(out.faci, [0.2, 0.1 * 200 / 300])
        np.testing.assert_allclose(out.fadi, [0.4, 0.2 * 160 / 240])

    def test_non_applicable_operator_does_not_hide_other_operator_calls(self):
        raw = source_rows().astype(object)
        for field in ['Scheduled stops', 'Recorded station stops', 'Recorded station stops (percentage)',
                      'Time to 3 (percentage)', 'Cancellations (percentage)']:
            raw.loc[0, field] = '[z]'
        out = aggregate_station_period(prepare_operator_period(raw, 80)).iloc[0]
        self.assertEqual(out.scheduled_stops, 900)
        self.assertEqual(out.recorded_stops, 800)
        self.assertEqual(out.cancellation_coverage_pct, 100)
        self.assertFalse(out.station_period_not_applicable)
        raw.loc[0, 'Cancellations (percentage)'] = '[u]'
        out = aggregate_station_period(prepare_operator_period(raw, 80)).iloc[0]
        self.assertTrue(pd.isna(out.scheduled_stops))

    def test_blank_identifiers_are_rejected(self):
        for field in ['Station name', 'Operator name', 'Time period']:
            raw = source_rows().copy()
            raw.loc[0, field] = '  '
            with self.assertRaisesRegex(ValueError, 'Missing'):
                prepare_operator_period(raw, 80)

    def test_locations_preserve_unmatched_and_reject_placeholders(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bplan.csv"
            pd.DataFrame({"tiploc": ["BRSTLTM", "COLESHL"], "location": ["Bristol Temple Meads", "Coleshill Parkway"],
                          "easting": [359700, 111111], "northing": [172500, 222222]}).to_csv(path, index=False)
            rows = pd.DataFrame({"station_name": ["Bristol Temple Meads", "Coleshill Parkway", "Missing station"], "scheduled_stops": [100, 50, 25]})
            out = attach_locations(rows, path, None).set_index("station_name")
            self.assertEqual(out.scheduled_stops.sum(), 175)
            self.assertEqual(out.loc["Coleshill Parkway", "location_status"], "invalid_or_missing_coordinate")
            self.assertTrue(np.isnan(out.loc["Coleshill Parkway", "longitude"]))
            self.assertEqual(out.loc["Coleshill Parkway", "bplan_coordinate_status"], "invalid_or_missing")
            self.assertTrue(pd.isna(out.loc["Coleshill Parkway", "coordinate_shift_m"]))
            self.assertEqual(out.loc["Missing station", "location_status"], "unmatched")
            self.assertAlmostEqual(out.loc["Bristol Temple Meads", "longitude"], -2.58, delta=.02)
            self.assertAlmostEqual(out.loc["Bristol Temple Meads", "latitude"], 51.45, delta=.02)

    def test_naptan_replaces_invalid_bplan_without_displacement(self):
        with tempfile.TemporaryDirectory() as directory:
            bplan, naptan = Path(directory) / 'bplan.csv', Path(directory) / 'naptan.csv'
            pd.DataFrame({'tiploc': ['TEST'], 'location': ['Test station'],
                          'easting': [999999], 'northing': [999999]}).to_csv(bplan, index=False)
            pd.DataFrame({'ATCOCode': ['9100TEST'], 'CommonName': ['Test station Rail Station'],
                          'StopType': ['RLY'], 'Status': ['active'], 'GridType': ['UKOS'],
                          'Easting': [359700], 'Northing': [172400]}).to_csv(naptan, index=False)
            out = attach_locations(pd.DataFrame({'station_name': ['Test station']}), bplan, None, naptan).iloc[0]
            self.assertEqual(out.bplan_easting, 999999)
            self.assertEqual(out.bplan_coordinate_status, 'invalid_or_missing')
            self.assertEqual(out.coordinate_source, 'NaPTAN')
            self.assertEqual(out.location_status, 'official_station_reference')
            self.assertEqual(out.easting, 359700)
            self.assertTrue(pd.isna(out.coordinate_shift_m))

    def test_ambiguous_names_require_explicit_tiploc(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bplan.csv"
            pd.DataFrame({"tiploc": ["A", "B"], "location": ["Shared", "Shared"], "easting": [359700, 360000], "northing": [172500, 173000]}).to_csv(path, index=False)
            out = attach_locations(pd.DataFrame({"station_name": ["Shared"]}), path, None).iloc[0]
            self.assertEqual(out.location_status, "ambiguous_name")
            self.assertTrue(pd.isna(out.tiploc))


if __name__ == "__main__":
    unittest.main()

class ApplicabilityTests(unittest.TestCase):
    def test_only_fully_z_rows_are_not_applicable(self):
        rows = source_rows().iloc[[0]].copy()
        fields = ['Scheduled stops', 'Recorded station stops', 'Recorded station stops (percentage)',
                  'Time to 3 (percentage)', 'Cancellations (percentage)']
        for field in fields:
            rows[field] = '[z]'
        out = aggregate_station_period(prepare_operator_period(rows, 80)).iloc[0]
        self.assertTrue(out.station_period_not_applicable)
        self.assertTrue(pd.isna(out.scheduled_stops))
        rows['Cancellations (percentage)'] = '[u]'
        out = aggregate_station_period(prepare_operator_period(rows, 80)).iloc[0]
        self.assertFalse(out.station_period_not_applicable)


class WindowTests(unittest.TestCase):
    def inputs(self):
        a = source_rows()
        b = source_rows()
        b["Time period"] = "second"
        b["Scheduled stops"] = [200, 1800]
        b["Recorded station stops"] = [160, 1600]
        b["Cancellations (percentage)"] = [10, 5]
        rows = prepare_operator_period(pd.concat([a, b], ignore_index=True), 80)
        dates = pd.DataFrame({"time_period": [a["Time period"].iloc[0], "second"],
                              "period_start": ["2023-04-01", "2023-04-29"],
                              "period_end": ["2023-04-28", "2023-05-26"]})
        return rows, dates

    def test_window_weights_days_and_median(self):
        rows, dates = self.inputs()
        out = aggregate_station_window(rows, dates, minimum_periods=1).iloc[0]
        self.assertEqual(out.scheduled_stops, 3000)
        self.assertAlmostEqual(out.cancellations_pct, 100 * 220 / 3000)
        self.assertEqual(out.faci_eligible_periods, 2)
        self.assertEqual(out.faci_eligible_days, 56)
        self.assertAlmostEqual(out.exploratory_faci, 220 / 3000)
        self.assertEqual(out.period_days, 56)

    def test_quarter_period_threshold_and_reference_population(self):
        calendar = pd.read_csv(Path(__file__).resolve().parents[1] / 'inputs/reporting_periods.csv').iloc[:45]
        pieces = []
        for name, periods, calls in [('Below', 11, 100), ('Boundary', 12, 200), ('Full', 45, 400)]:
            for period in calendar.time_period.iloc[:periods]:
                r = source_rows().copy()
                r['Station name'] = name
                r['Time period'] = period
                r['Scheduled stops'] = calls
                r['Recorded station stops'] = calls
                pieces.append(r)
        out = aggregate_station_window(prepare_operator_period(pd.concat(pieces), 80), calendar).set_index('station_name')
        self.assertEqual(out.loc['Below', 'minimum_index_periods'], 12)
        self.assertTrue(pd.isna(out.loc['Below', 'exploratory_faci']))
        self.assertTrue(pd.isna(out.loc['Below', 'exploratory_fadi']))
        self.assertGreater(out.loc['Below', 'scheduled_stops'], 0)
        self.assertTrue(out.loc['Boundary', 'faci_periods_sufficient'])
        f = out.loc[['Boundary', 'Full'], 'exploratory_scheduled_stops_per_eligible_day']
        self.assertAlmostEqual(out.loc['Full', 'exploratory_reference_median_scheduled_stops_per_day'], f.median())

    def test_station_and_area_thresholds_follow_the_observation_window(self):
        from src.data.areas import geographic_indices
        for count, required in [(8, 2), (45, 12), (49, 13)]:
            starts = pd.date_range('2026-01-01', periods=count, freq='28D')
            dates = pd.DataFrame({'time_period':[f'Period {i}' for i in range(count)],
                'period_start':starts.strftime('%Y-%m-%d'),
                'period_end':(starts + pd.Timedelta(days=27)).strftime('%Y-%m-%d')})
            pieces = []
            for period in dates.time_period:
                row = source_rows().copy()
                row['Time period'] = period
                pieces.append(row)
            rows = prepare_operator_period(pd.concat(pieces, ignore_index=True), 80)
            for usable, sufficient in [(required - 1, False), (required, True)]:
                eligible = rows.time_period.isin(dates.time_period.iloc[:usable])
                rows['faci_eligible'], rows['fadi_eligible'] = eligible, eligible
                window = aggregate_station_window(rows, dates)
                self.assertEqual(window.minimum_index_periods.iloc[0], required)
                self.assertEqual(bool(window.fadi_periods_sufficient.iloc[0]), sufficient)
                periods = aggregate_station_period(rows).merge(dates, on='time_period')
                periods['period_days'] = 28
                stations = pd.DataFrame({'station_name':['Test station'], 'area_code':['X']})
                areas = window[['scheduled_stops','recorded_stops','period_start','period_end','period_days']].assign(area_code='X', area_name='Example')
                indices = geographic_indices(window, periods, stations, areas)
                self.assertEqual(indices.minimum_index_periods.iloc[0], required)
                self.assertEqual(bool(indices.fadi_periods_sufficient.iloc[0]), sufficient)

    def test_each_index_has_its_own_quarter_period_filter(self):
        calendar = pd.read_csv(Path(__file__).resolve().parents[1] / 'inputs/reporting_periods.csv').iloc[:45]
        pieces = []
        for i, period in enumerate(calendar.time_period):
            row = source_rows().astype(object)
            row['Time period'] = period
            row['Recorded station stops (percentage)'] = 100 if i < 11 else 39.7
            pieces.append(row)
        rows = prepare_operator_period(pd.concat(pieces, ignore_index=True), 80)
        out = aggregate_station_window(rows, calendar).iloc[0]
        self.assertEqual(out.faci_eligible_periods, 45)
        self.assertEqual(out.fadi_eligible_periods, 11)
        self.assertTrue(pd.notna(out.exploratory_faci))
        self.assertTrue(pd.isna(out.exploratory_fadi))
        rows.loc[rows.time_period.eq(calendar.time_period.iloc[11]), 'fadi_eligible'] = True
        boundary = aggregate_station_window(rows, calendar).iloc[0]
        self.assertEqual(boundary.fadi_eligible_periods, 12)
        self.assertTrue(pd.notna(boundary.exploratory_fadi))

    def test_missing_applicable_count_stays_missing(self):
        rows, dates = self.inputs()
        rows.loc[0, "scheduled_stops"] = np.nan
        out = aggregate_station_window(rows, dates, minimum_periods=1).iloc[0]
        self.assertTrue(pd.isna(out.scheduled_stops))

    def test_window_calendar_gap_rejected(self):
        rows, dates = self.inputs()
        dates.loc[1, "period_start"] = "2023-04-30"
        with self.assertRaises(ValueError):
            aggregate_station_window(rows, dates, minimum_periods=1)


    def test_daily_indices_ignore_unequal_eligible_duration(self):
        rows, dates = self.inputs()
        first = rows.loc[rows.time_period.eq(dates.time_period.iloc[0])].copy()
        second = first.copy()
        second["time_period"] = "second"
        a = pd.concat([first, second], ignore_index=True)
        b = a.copy()
        b["station_name"] = "Short coverage"
        b.loc[b.time_period.eq("second"), ["faci_eligible", "fadi_eligible"]] = False
        out = aggregate_station_window(pd.concat([a, b], ignore_index=True), dates, minimum_periods=1).set_index("station_name")
        self.assertEqual(out.loc["Short coverage", "faci_eligible_days"], 28)
        self.assertEqual(out.loc["Test station", "faci_eligible_days"], 56)
        self.assertAlmostEqual(out.loc["Short coverage", "exploratory_faci"], out.loc["Test station", "exploratory_faci"])
        self.assertAlmostEqual(out.loc["Short coverage", "exploratory_fadi"], out.loc["Test station", "exploratory_fadi"])

    def test_reversed_single_period_calendar_rejected(self):
        rows, dates = self.inputs()
        rows = rows.loc[rows.time_period.eq(dates.time_period.iloc[0])]
        dates = dates.iloc[[0]].copy()
        dates['period_end'] = '2023-03-31'
        with self.assertRaisesRegex(ValueError, 'Invalid reporting dates'):
            aggregate_station_window(rows, dates, minimum_periods=1)

    def test_ineligible_source_period_still_requires_calendar(self):
        rows, dates = self.inputs()
        rows.loc[rows.time_period.eq('second'), ['faci_eligible', 'fadi_eligible']] = False
        with self.assertRaisesRegex(ValueError, 'missing reporting periods'):
            aggregate_station_window(rows, dates.iloc[[0]])
