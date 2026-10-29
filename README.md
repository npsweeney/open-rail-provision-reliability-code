# Open Rail Provision and Reliability

Rail frequency, cancellations, delays and frequency-adjusted disruption indices for Great Britain, from stations to LADs and ITL regions, with TTWAs as an alternative geography.

Requires **Git, Python 3.10+ and R**.

```bash
git clone https://github.com/npsweeney/open-rail-provision-reliability-code.git
cd open-rail-provision-reliability-code
./run.sh "$HOME/Desktop/rail-results"
```

Choose a new output directory. The script installs dependencies, downloads public inputs, builds the data, validates station matches and renders figures and tables.

Results are saved in `processed/`, `validation/` and `figures/`. Downloads are cached in `data/`.

Each run refreshes ORR and NaPTAN. For new releases, add confirmed dates to `inputs/reporting_periods.csv` and review any new station-name matches.

[Station geocoding checks](station-geocoding-checks)

