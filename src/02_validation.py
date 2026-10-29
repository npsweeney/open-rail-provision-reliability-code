#!/usr/bin/env python3
"""Validate station matches and optionally compare an authorised BPLAN extract."""
import argparse
import json
from pathlib import Path
from data.validation import validate


# Read the completed CSV directory and run the station-reference comparison
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--outdir', type=Path, required=True)
    parser.add_argument('--bplan', type=Path)
    parser.add_argument('--lad-boundaries', type=Path, help='Official LAD GeoJSON or GeoParquet for independent coordinate-membership comparison')
    args = parser.parse_args()
    mapping = Path(__file__).resolve().parents[1]/'inputs/station_mapping.csv'
    print(json.dumps(validate(args.source, args.outdir, args.bplan, mapping, args.lad_boundaries), indent=2))


if __name__ == '__main__':
    main()
