# Render the TTWA provision, reliability and index maps from processed CSVs
suppressPackageStartupMessages(library(ggplot2))
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2L) stop('Usage: Rscript src/figures/ttwa.R TTWA_DIRECTORY OUTPUT_DIRECTORY')
source_dir <- args[1]
outdir <- args[2]
dir.create(outdir, showWarnings = FALSE, recursive = TRUE)
rows <- read.csv(file.path(source_dir, 'ttwa_summary.csv'))
vertices <- read.csv(file.path(source_dir, 'map_vertices.csv'))
stopifnot(!anyDuplicated(rows$area_code), all(vertices$area_code %in% rows$area_code),
          all(rows$population_year == 2024), all(rows$population_2024 > 0))
number <- scales::label_number(big.mark = ',', trim = TRUE)
colours <- viridisLite::viridis(256, begin = .05, end = .95)

# Draw a TTWA measure using its own scale
map <- function(field, units, logarithmic = FALSE, index_sigma = NULL, index_breaks = NULL) {
  d <- vertices
  d$value <- rows[[field]][match(d$area_code, rows$area_code)]
  if (logarithmic) d$value[d$value == 0] <- NA
  colour_transform <- if (!is.null(index_sigma)) scales::pseudo_log_trans(sigma = index_sigma, base = 10) else if (logarithmic) 'log10' else 'identity'
  colour_breaks <- if (!is.null(index_breaks)) index_breaks else if (logarithmic) scales::breaks_log(n = 4) else scales::breaks_pretty(n = 4)
  colour_limits <- if (!is.null(index_sigma)) c(0, max(c(index_breaks, d$value), na.rm = TRUE)) else NULL
  ggplot(d, aes(x, y, group = piece, subgroup = ring, fill = value)) +
    geom_polygon(rule = 'evenodd', colour = '#909B99', linewidth = .14) +
    coord_equal() +
    scale_fill_gradientn(colours = colours, name = units, na.value = '#DDDDDD',
      transform = colour_transform, limits = colour_limits,
      labels = number, breaks = colour_breaks) +
    labs(caption = paste0('ORR: ', min(rows$period_start), ' to ', max(rows$period_end))) +
    theme_void(base_size = 11) +
    theme(plot.title = element_blank(), plot.subtitle = element_blank(),
          plot.caption = element_text(size = 7, colour = '#526168', hjust = .5),
          legend.position = 'bottom', legend.title = element_text(size = 13),
          legend.text = element_text(size = 13), plot.margin = margin(8, 6, 8, 6)) +
    guides(fill = guide_colourbar(title.position = 'top', barwidth = grid::unit(4.3, 'in'),
                                  barheight = grid::unit(.12, 'in')))
}
plots <- list(
  provision = map('scheduled_calls_per_10000_residents_per_day', 'Scheduled calls / 10,000 residents / day (log scale)', TRUE),
  cancellations = map('cancellations_pct', 'Cancelled (%)'),
  delays = map('delayed_pct', 'Delayed (%)'),
  faci = map('geographic_faci', 'FACI', index_sigma = .005, index_breaks = c(0, .01, .03, .1, .3)),
  fadi = map('geographic_fadi', 'FADI', index_sigma = .02, index_breaks = c(0, .03, .1, .3, 1, 3.5))
)
for (name in names(plots)) {
  cat('Rendering ttwa_', name, ' (PNG and PDF)\n', sep = '')
  ggsave(file.path(outdir, paste0('ttwa_', name, '.png')), plots[[name]], bg = 'white', width = 6.5, height = 9, dpi = 190)
  ggsave(file.path(outdir, paste0('ttwa_', name, '.pdf')), plots[[name]], bg = 'white', width = 6.5, height = 9, device = grDevices::pdf, useDingbats = FALSE)
}
# Save a row of plots as PDF and PNG
save_panels <- function(selected, name, width = 4.3 * length(selected), height = 8) {
  cat('Rendering ', name, ' (PNG and PDF)\n', sep = '')
  progress <- if (isatty(stdout())) txtProgressBar(min = 0, max = 2, style = 3) else NULL
  on.exit(if (!is.null(progress)) close(progress))
  # Place each plot in its panel
  draw <- function() {
    grid::grid.newpage()
    grid::pushViewport(grid::viewport(layout = grid::grid.layout(1, length(selected))))
    for (i in seq_along(selected)) print(selected[[i]], vp = grid::viewport(layout.pos.row = 1, layout.pos.col = i))
    grid::popViewport()
  }
  png(file.path(outdir, paste0(name, '.png')), width = width, height = height, units = 'in', res = 180)
  draw(); dev.off()
  if (!is.null(progress)) setTxtProgressBar(progress, 1)
  grDevices::pdf(file.path(outdir, paste0(name, '.pdf')), width = width, height = height, useDingbats = FALSE)
  draw(); dev.off()
  if (!is.null(progress)) setTxtProgressBar(progress, 2)
}
save_panels(plots[c('provision','cancellations','delays')], 'ttwa_standard_maps')
save_panels(plots[c('faci','fadi')], 'ttwa_index_maps')
# Compact page panels for the summary PDF
compact <- function(p, count) {
  p + theme(legend.title = element_text(size = 7), legend.text = element_text(size = 6),
            plot.caption = element_blank(), plot.margin = margin(3, 2, 3, 2)) +
    guides(fill = guide_colourbar(title.position = 'top',
      barwidth = grid::unit(if (count == 3) 1.2 else 1.8, 'in'), barheight = grid::unit(.10, 'in')))
}
standard_labels <- c('Calls/10k/day', 'Cancelled (%)', 'Delayed (%)')
standard_compact <- Map(function(field, label, islog) compact(map(field, label, logarithmic = islog), 3),
  c('scheduled_calls_per_10000_residents_per_day','cancellations_pct','delayed_pct'),
  standard_labels, c(TRUE, FALSE, FALSE))
save_panels(standard_compact, 'ttwa_standard_page_maps', width = 4.8, height = 2.55)
save_panels(lapply(plots[c('faci','fadi')], compact, count = 2),
  'ttwa_index_page_maps', width = 4.8, height = 2.35)

cat('TTWA maps:', outdir, '\n')
