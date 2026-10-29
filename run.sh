#!/bin/sh
# Prepare dependencies and run every stage into the requested output directory
set -eu

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s /path/to/results\n' "$0" >&2
  exit 1
fi
for program in python3 Rscript; do
  if ! command -v "$program" >/dev/null 2>&1; then
    printf 'Install %s before running this pipeline\n' "$program" >&2
    exit 1
  fi
done

repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
output=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$1")
if [ -e "$output" ]; then
  printf 'Choose a new output directory: %s already exists\n' "$output" >&2
  exit 1
fi

# Install Python packages in the checkout and missing R packages in the user library
python3 -m venv "$repo/.venv"
python="$repo/.venv/bin/python"
"$python" -m pip install -r "$repo/requirements.txt"
Rscript -e '
packages <- c("ggplot2", "scales", "viridisLite", "ggrepel")
missing <- setdiff(packages, rownames(installed.packages()))
if (length(missing)) {
  library <- path.expand(Sys.getenv("R_LIBS_USER"))
  dir.create(library, recursive = TRUE, showWarnings = FALSE)
  install.packages(missing, lib = library, repos = "https://cloud.r-project.org")
  .libPaths(c(library, .libPaths()))
  if (!all(vapply(packages, requireNamespace, logical(1), quietly = TRUE))) stop("R package installation failed")
}'

# Download public inputs and build the CSVs, validation and figures in order
"$python" "$repo/src/00_inputs.py" --root "$repo" --ttwa --refresh
"$python" "$repo/src/01_data.py" \
  --root "$repo" \
  --orr "$repo/data/raw/table-3130-time-to-3-and-cancellations-by-station-and-operator.ods" \
  --ttwa "$repo/data/ttwa/ttwa_population_2024.csv" \
  --outdir "$output"
"$python" "$repo/src/02_validation.py" --source "$output/processed" --outdir "$output/validation"
Rscript "$repo/src/figures/figures.R" "$output/processed" "$output/figures"
printf '\nResults: %s\n' "$output"
