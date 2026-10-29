#!/usr/bin/env python3
"""Download the public inputs needed for local processing."""
import argparse
from pathlib import Path
from data.download import download_inputs


# Download public source files and prepare the required input formats
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--ttwa', action='store_true', help='Also prepare TTWA boundaries and population')
    parser.add_argument('--refresh', action='store_true', help='Replace the ORR and NaPTAN downloads')
    parser.add_argument('--orr-url', help='Use a specific ORR workbook URL instead of the current release')
    args = parser.parse_args()
    download_inputs(args.root, args.ttwa, args.refresh, args.orr_url)


if __name__ == '__main__':
    main()
