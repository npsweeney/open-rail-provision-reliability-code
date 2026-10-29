#!/usr/bin/env python3
"""Build station and geographic CSVs from the ORR workbook."""
import argparse
from pathlib import Path
from data.build import build

REPO = Path(__file__).resolve().parents[1]


# Read the input paths and build the article CSVs
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--orr', type=Path, required=True)
    parser.add_argument('--calendar', type=Path, default=REPO/'inputs/reporting_periods.csv')
    parser.add_argument('--naptan', type=Path)
    parser.add_argument('--ttwa', type=Path, help='Optional mid-2024 TTWA population CSV')
    parser.add_argument('--quality-min', type=float, default=80)
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args()
    if args.outdir.exists():
        parser.error('Use a new output directory to preserve completed runs.')
    raw = args.root/'data/raw'
    inputs = {'orr': args.orr, 'calendar': args.calendar,
        'station-mapping': REPO/'inputs/station_naptan_mapping.csv',
        'naptan': args.naptan or raw/'naptan_rail.csv',
        'area-boundaries': raw/'lad_2024.parquet',
        'population': raw/'ons_mye2024_uk.xlsx',
        'itl-lookup': raw/'lad24_itl25_lookup.csv',
        'itl-boundaries': raw/'itl_2025_official',
        'scottish-itl-population': raw/'nrs_special_area_population_2022_2024_dz11.xlsx'}
    if args.ttwa:
        inputs['ttwa-population'] = args.ttwa
    for name, path in inputs.items():
        if not path.exists():
            parser.error(f'Missing {name}: {path}')
    processed = args.outdir/'processed'
    build(**{name.replace('-', '_'): path for name, path in inputs.items()},
          quality_min=args.quality_min, outdir=processed)
    print(f'CSVs: {processed}')


if __name__ == "__main__":
    main()
