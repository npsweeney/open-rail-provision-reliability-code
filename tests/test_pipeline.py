"""Exercise the documented processing entry point in an isolated output directory."""
import tempfile
import unittest
from pathlib import Path
import pandas as pd

from src.data.build import build


class PipelineTests(unittest.TestCase):
    def test_build_preserves_counts_and_ignores_stale_figure_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workbook, calendar, output = root / 'orr.xlsx', root / 'calendar.csv', root / 'outputs'
            output.mkdir()
            # A previous optional output must not trigger figures from a reduced run
            (output / 'itl1_orr_provision_window.csv').write_text('stale file')
            pd.DataFrame({
                'Time period': ['P1', 'P1'], 'Station name': ['Example', 'Example'],
                'Operator name': ['A', 'B'], 'Scheduled stops': [100, 900],
                'Recorded station stops': [80, 800], 'Recorded station stops (percentage)': [50, 100],
                'Time to 3 (percentage)': [50, 90], 'Cancellations (percentage)': [20, 10]
            }).to_excel(workbook, index=False)
            pd.DataFrame({'time_period': ['P1'], 'period_start': ['2026-01-01'],
                          'period_end': ['2026-01-28']}).to_csv(calendar, index=False)
            build(orr=workbook, sheet='Sheet1', calendar=calendar, outdir=output)
            station = pd.read_csv(output / 'station_window.csv').iloc[0]
            self.assertEqual(station.scheduled_stops, 1000)
            self.assertEqual(station.recorded_stops, 880)
            self.assertAlmostEqual(station.cancellations_pct, 11)
            self.assertEqual(station.faci_eligible_periods, 1)
            self.assertEqual(station.fadi_eligible_periods, 1)
            self.assertEqual(station.minimum_index_periods, 1)
            self.assertAlmostEqual(station.exploratory_faci, .11)
            self.assertAlmostEqual(station.exploratory_fadi, .10)
            self.assertFalse((output / 'figure_data').exists())
