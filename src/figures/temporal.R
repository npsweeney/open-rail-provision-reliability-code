# Compare four-weekly cancellation rates across ITL2 areas
suppressPackageStartupMessages(library(ggplot2))
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2L) stop('Usage: Rscript src/figures/temporal.R SOURCE_DIR OUTPUT_DIR')
rows <- read.csv(file.path(args[1], 'itl2_orr_provision.csv'))
stopifnot(!anyDuplicated(rows[c('area_code', 'time_period')]))
rows$date <- as.Date(rows$period_start)
years <- seq(as.integer(format(min(rows$date), '%Y')), as.integer(format(max(rows$date), '%Y')))
axis_years <- if (length(years) > 1L) years[seq_along(years) %% 2L == 0L] else years
rows$cancellations_pct <- 100 * rows$estimated_cancelled_calls / rows$cancellation_observed_scheduled_stops
stopifnot(all(is.na(rows$cancellations_pct) | (rows$cancellations_pct >= 0 & rows$cancellations_pct <= 100)))
rows$label <- vapply(rows$area_name, function(x) paste(strwrap(x, width = 23), collapse = '\n'), character(1))
p <- ggplot(rows, aes(date, cancellations_pct, group = area_code)) +
  geom_line(colour = '#286682', linewidth = .35, na.rm = TRUE) +
  facet_wrap(~label, ncol = 5) +
  scale_x_date(breaks = as.Date(paste0(axis_years, '-01-01')), date_labels = '%Y') +
  scale_y_continuous(limits = c(0, NA), breaks = scales::breaks_pretty(n = 3)) +
  labs(x = NULL, y = 'Cancelled calls (%)') +
  theme_minimal(base_size = 8) +
  theme(panel.grid.minor = element_blank(), panel.grid.major = element_line(linewidth = .2),
        strip.text = element_text(size = 7, face = 'bold'), axis.text = element_text(size = 6),
        panel.spacing = grid::unit(.65, 'lines'), plot.margin = margin(5, 5, 5, 5))
dir.create(args[2], recursive = TRUE, showWarnings = FALSE)
ggsave(file.path(args[2], 'itl2_cancellations_temporal.pdf'), p, width = 7.2, height = 9,
       device = grDevices::pdf, useDingbats = FALSE, bg = 'white')
ggsave(file.path(args[2], 'itl2_cancellations_temporal.png'), p, width = 7.2, height = 9, dpi = 180, bg = 'white')
