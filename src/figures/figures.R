# Draw figures and write tables from completed CSVs
suppressPackageStartupMessages(library(ggplot2))
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2L) stop('Usage: Rscript src/figures/figures.R SOURCE_DIR OUTPUT_DIR')
source_dir <- args[1]; output_dir <- args[2]
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
# Read one completed CSV without changing its column names
read_csv <- function(name, directory = source_dir) read.csv(file.path(directory, name), check.names = FALSE, stringsAsFactors = FALSE)
figure_data_dir <- file.path(source_dir, 'figure_data')
pooled <- read_csv('station_window.csv'); national <- read_csv('national_period.csv', figure_data_dir)
vertices <- read_csv('map_vertices.csv')
itl <- read_csv('itl1_orr_provision_window.csv')
lad <- read_csv('lad_orr_provision_window.csv')
lad_itl <- unique(read_csv('lad_itl1_lookup.csv'))
stopifnot(!anyDuplicated(lad_itl$LAD24CD))
lad$ITL1 <- lad_itl$ITL125NM[match(lad$area_code, lad_itl$LAD24CD)]
stopifnot(!anyNA(lad$ITL1))
region_names <- itl$area_name[order(itl$area_code)]
region_colours <- setNames(c('#0072B2', '#D55E00', '#009E73', '#CC3377', '#111111',
  '#C18A00', '#40A4D8', '#882255', '#145A32', '#A6761D', '#657786'), region_names)
region_shapes <- setNames(c(16, 17, 15, 18, 3, 4, 8, 0, 1, 2, 5), region_names)
# Shorten ITL names for figure labels
region_labels <- function(x) gsub(' (England)', '', gsub('Yorkshire and The Humber', 'Yorkshire & Humber', x, fixed = TRUE), fixed = TRUE)
viridis <- viridisLite::viridis(256, begin = .05, end = .95)
data_colour <- viridis[115]; fit_colour <- viridis[210]; ink <- '#24343B'
missing_grey <- '#DDDDDD'
number <- scales::label_number(big.mark = ',', decimal.mark = '.', trim = TRUE)
log_breaks <- scales::breaks_log(n = 5)
# Use a logarithmic x axis with ordinary numeric labels
log_x <- function() scale_x_log10(labels = number, breaks = log_breaks)
# Use a logarithmic y axis with ordinary numeric labels
log_y <- function() scale_y_log10(labels = number, breaks = log_breaks)
# Apply the shared figure style
theme_figures <- function() theme_minimal(base_size = 10.5, base_family = 'sans') +
  theme(panel.grid.minor = element_blank(), panel.grid.major = element_line(colour = '#E5E7E5', linewidth = .25),
        axis.text = element_text(colour = '#526168'), axis.title = element_text(colour = ink),
        axis.title.x = element_text(margin = margin(t = 7)), axis.title.y = element_text(margin = margin(r = 7)),
        plot.margin = margin(8, 10, 7, 8), plot.title = element_blank(), plot.subtitle = element_blank(),
        legend.position = 'bottom', legend.title = element_text(size = 9), legend.text = element_text(size = 8))
theme_set(theme_figures())
# Save a row of plots as PDF and PNG
save_panels <- function(plots, name, width = 8.8, height = 3.3) {
  cat('Rendering ', name, ' (PNG and PDF)\n', sep = '')
  progress <- if (isatty(stdout())) txtProgressBar(min = 0, max = 2, style = 3) else NULL
  on.exit(if (!is.null(progress)) close(progress))
  # Place each plot in its panel
  draw <- function() {
    grid::grid.newpage()
    grid::pushViewport(grid::viewport(layout = grid::grid.layout(1, length(plots))))
    for (i in seq_along(plots)) {
      vp <- grid::viewport(layout.pos.row = 1, layout.pos.col = i)
      if (inherits(plots[[i]], 'ggplot')) print(plots[[i]], vp = vp) else {
        grid::pushViewport(vp); grid::grid.draw(plots[[i]]); grid::popViewport()
      }
    }
    grid::popViewport()
  }
  grDevices::pdf(file.path(output_dir, paste0(name, '.pdf')), width = width, height = height, useDingbats = FALSE)
  draw(); dev.off()
  if (!is.null(progress)) setTxtProgressBar(progress, 1)
  png(file.path(output_dir, paste0(name, '.png')), width = width, height = height, units = 'in', res = 190)
  draw(); dev.off()
  if (!is.null(progress)) setTxtProgressBar(progress, 2)
}
# Booktabs tables; numerics have explicit precision, and extremes retain their separating rule
tex_escape <- function(x) {
  x <- gsub('&', '\\&', as.character(x), fixed = TRUE)
  gsub('_', '\\_', x, fixed = TRUE)
}
# Format table values and show missing entries explicitly
fmt <- function(x, digits = 2) ifelse(is.na(x), '---', formatC(x, format = 'f', digits = digits, big.mark = ','))
# Write a booktabs table from the supplied rows
write_table <- function(data, labels, name, alignment, split_after = NULL) {
  lines <- c(paste0('\\begin{tabular}{@{}', alignment, '@{}}'), '\\toprule',
             paste0(paste(labels, collapse = ' & '), ' \\\\'), '\\midrule')
  for (i in seq_len(nrow(data))) {
    if (!is.null(split_after) && i == split_after + 1L) lines <- c(lines, '\\midrule')
    lines <- c(lines, paste0(paste(unlist(data[i, ], use.names = FALSE), collapse = ' & '), ' \\\\'))
  }
  writeLines(c(lines, '\\bottomrule', '\\end{tabular}'), file.path(output_dir, paste0(name, '.tex')))
}
summary <- read_csv('headline_summary.csv', figure_data_dir)
write_table(summary, c('Measure', 'Value', 'Coverage \\%'), 'headline', 'lrr')
for (level in c('itl1', 'lad')) {
  data <- read_csv(paste0(level, '_display_rows.csv'), figure_data_dir)
  table <- data.frame(tex_escape(data$area_name), fmt(data$scheduled_calls_per_10000_residents),
                      fmt(data$cancellations_pct), fmt(data$outside_time_to_3_pct),
                      fmt(data$scheduled_station_coverage_pct), fmt(data$not_applicable_station_count, 0))
  write_table(table, c('Area', 'Calls/10k/day', 'Cancel \\%', 'Delayed \\%', 'Count \\%', '[z] stn-periods'), level, 'lrrrrr')
}
extremes <- read_csv('index_extremes.csv', figure_data_dir)
for (measure in c('FACI', 'FADI')) {
  data <- extremes[extremes$Index == measure, ]
  table <- data.frame(fmt(data$Rank, 0), tex_escape(data$station_name), tex_escape(data$LAD), fmt(data$Periods, 0),
                      fmt(data[['Calls/day']], 3), fmt(data$Rate, 3), fmt(data$Score, 5))
  write_table(table, c('Rank', 'Station', 'LAD', 'Periods', 'Calls/day', if (measure == 'FACI') 'Cancel \\%' else 'Delayed \\%', measure),
              paste0(tolower(measure), '_ranking'), 'rllrrrr', split_after = 5)
}
# Geographic indices are calculated in Python from eligible station-days
geographic <- setNames(lapply(c('itl1', 'lad'), function(level) read_csv(paste0(level, '_geographic_indices.csv'))), c('itl1', 'lad'))
for (level in names(geographic)) {
  data <- geographic[[level]]
  data$display_name <- gsub('Yorkshire and The Humber', 'Yorkshire & Humber', data$area_name, fixed = TRUE)
  data$display_name <- gsub('Edinburgh and South East Scotland', 'Edinburgh & SE Scotland', data$display_name, fixed = TRUE)
  if (level != 'lad') {
    data <- data[order(data$geographic_faci, decreasing = TRUE, na.last = TRUE), ]
    cells <- data.frame(tex_escape(data$display_name), fmt(data$index_scheduled_calls_per_station_day, 1),
      fmt(data$index_recorded_calls_per_station_day, 1), fmt(data$index_cancellations_pct), fmt(data$index_delay_pct),
      fmt(data$geographic_faci, 3), fmt(data$geographic_fadi, 3), fmt(data$faci_station_day_coverage_pct, 1), fmt(data$fadi_station_day_coverage_pct, 1))
    write_table(cells, c('Area', 'Sched/day', 'Rec/day', 'Cancel \\%', 'Delayed \\%', 'FACI', 'FADI', 'FACI days \\%', 'FADI days \\%'),
      paste0(level, '_indices'), 'lrrrrrrrr')
  } else {
    for (measure in c('faci', 'fadi')) {
      score <- paste0('geographic_', measure)
      ranked <- data[is.finite(data[[score]]), ]
      ranked <- ranked[order(-ranked[[score]], ranked$area_name), ]
      ranked$Rank <- seq_len(nrow(ranked))
      chosen <- rbind(head(ranked, 5), tail(ranked, 5))
      volume <- if (measure == 'faci') 'scheduled' else 'recorded'
      rate <- if (measure == 'faci') 'index_cancellations_pct' else 'index_delay_pct'
      cells <- data.frame(fmt(chosen$Rank, 0), tex_escape(chosen$display_name),
        fmt(chosen[[paste0('index_', volume, '_calls_per_station_day')]], 2),
        fmt(chosen[[rate]], 2), fmt(chosen[[score]], 4), fmt(chosen[[paste0(measure, '_station_day_coverage_pct')]], 1))
      write_table(cells, c('Rank', 'LAD', 'Calls/stn/day', if (measure == 'faci') 'Cancel \\%' else 'Delayed \\%',
        toupper(measure), 'Elig. \\%'), paste0('lad_', measure, '_ranking'), 'rlrrrr', split_after = 5)
      write.csv(chosen, file.path(output_dir, paste0('lad_', measure, '_ranking.csv')), row.names = FALSE)
    }
  }
  reference <- sprintf('Reference medians: %.3f scheduled and %.3f recorded calls per eligible station-day. FACI uses available cancellations and positive scheduled calls; FADI uses available punctuality, positive recorded calls and recording completeness of at least 80 percent. Each score requires at least 12 eligible periods. Separate coverage columns give the eligible share of applicable station-days. Scores compare areas within this geographic level.',
    data$reference_median_scheduled_calls_per_station_day[1], data$reference_median_recorded_calls_per_station_day[1])
  reference <- paste0(reference, sprintf(' Minimum observed-call coverage within the eligible sample: cancellations %.1f\\%%; delays %.1f\\%%.',
    min(data$index_scheduled_call_coverage_pct, na.rm = TRUE), min(data$index_recorded_call_coverage_pct, na.rm = TRUE)))
  writeLines(reference, file.path(output_dir, paste0(level, '_index_reference.tex')))
  write.csv(data, file.path(output_dir, paste0(level, '_geographic_indices.csv')), row.names = FALSE)
}
# Preserve linear station-rate histograms and log station-count index histograms
rate_labels <- c('Cancelled calls (%)', 'Delayed (>= 3 mins)')
trend_plots <- lapply(seq_along(rate_labels), function(i) {
  field <- c('cancellations_pct', 'outside_time_to_3_pct')[i]
  ggplot(national, aes(x = as.Date(period_start), y = .data[[field]])) +
    geom_line(colour = data_colour, linewidth = .7) + labs(x = NULL, y = rate_labels[i]) +
    scale_x_date(date_breaks = '6 months', date_labels = '%b %Y') +
    theme(axis.text.x = element_text(angle = 30, hjust = 1))
})
save_panels(trend_plots, 'national_trends')
# Plot the station distribution on the requested scale
histogram <- function(values, breaks, log_count = FALSE, xlab) {
  values <- values[is.finite(values)]
  h <- hist(values, breaks = breaks, plot = FALSE, right = FALSE, include.lowest = TRUE)
  d <- data.frame(left = head(h$breaks, -1), right = tail(h$breaks, -1), count = h$counts)
  if (log_count) d <- d[d$count > 0, ]
  p <- ggplot(d) + geom_rect(aes(xmin = left, xmax = right, ymin = if (log_count) .8 else 0, ymax = count),
                             fill = data_colour, colour = 'white', linewidth = .22) +
    labs(x = xlab, y = if (log_count) 'Stations (log scale)' else 'Stations (equal weight)')
  if (log_count) p <- p + log_y() else p <- p + scale_y_continuous(labels = number)
  p
}
save_panels(lapply(1:2, function(i) histogram(pooled[[c('cancellations_pct', 'delay_pct')[i]]], seq(0, 100, 2), xlab = rate_labels[i])), 'station_distributions')
station_index_plots <- lapply(c('faci', 'fadi'), function(m) {
  v <- pooled[[paste0('exploratory_', m)]]; v <- v[is.finite(v)]
  histogram(v, seq(min(v), max(v), length.out = 36), TRUE, toupper(m))
})
save_panels(station_index_plots, 'station_index_distributions')
# Linear fits and Pearson r use log10 daily calls, matching the displayed x scale
fit_rows <- list(); association_plots <- list()
for (i in 1:2) {
  volume <- c('scheduled_calls_per_calendar_day', 'recorded_calls_per_calendar_day')[i]
  rate <- c('cancellations_pct', 'delay_pct')[i]
  data <- data.frame(x = pooled[[volume]], y = pooled[[rate]])
  data <- data[is.finite(data$x) & data$x > 0 & is.finite(data$y), ]
  fit <- lm(y ~ log10(x), data = data); r <- cor(log10(data$x), data$y)
  slope_p <- summary(fit)$coefficients[2, 'Pr(>|t|)']
  slope_stars <- if (is.finite(slope_p) && slope_p < .01) '***' else ''
  slope_label <- sprintf('%.3f%s', unname(coef(fit)[2]), slope_stars)
  fit_rows[[i]] <- data.frame(measure = c('cancellation', 'delay')[i], x_transform = 'log10', n = nrow(data),
                              pearson_r = r, slope = unname(coef(fit)[2]), intercept = unname(coef(fit)[1]),
                              slope_p_value = slope_p, slope_stars = slope_stars, slope_label = slope_label)
  line <- data.frame(x = exp(seq(log(min(data$x)), log(max(data$x)), length.out = 200)))
  line$y <- predict(fit, newdata = line)
  association_plots[[i]] <- ggplot(data, aes(x, y)) +
    geom_point(colour = data_colour, alpha = .3, size = 1) +
    geom_line(data = line, colour = fit_colour, linewidth = .8) + log_x() +
    annotate('text', x = Inf, y = Inf, label = sprintf('OLS slope = %s\nPearson r (log10 calls) = %.3f\nn = %s', slope_label, r, number(nrow(data))),
             hjust = 1.05, vjust = 1.2, size = 3.1, colour = ink) +
    labs(x = paste(c('Scheduled', 'Recorded')[i], 'calls/day (log scale)'), y = rate_labels[i],
         caption = '*** p < 0.01; two-sided OLS slope test')
}
write.csv(do.call(rbind, fit_rows), file.path(output_dir, 'reliability_frequency_fits.csv'), row.names = FALSE)
save_panels(association_plots, 'reliability_vs_frequency')
# Area-level associations: equal area weights, using all observed area rates
area_fits <- list()
for (level in c('itl1', 'lad')) {
  rows <- switch(level, itl1 = itl, lad = lad)
  plots <- list()
  for (i in 1:2) {
    field <- c('cancellations_pct', 'outside_time_to_3_pct')[i]
    data <- data.frame(area_code = rows$area_code, area_name = rows$area_name,
                       x = rows$scheduled_calls_per_10000_residents, y = rows[[field]])
    if (level == 'lad') data$ITL1 <- rows$ITL1
    data <- data[is.finite(data$x) & is.finite(data$y), ]
    logarithmic <- level == 'lad'
    if (logarithmic) data <- data[data$x > 0, ]
    data$fit_x <- if (logarithmic) log10(data$x) else data$x
    fit <- lm(y ~ fit_x, data = data); r <- cor(data$fit_x, data$y)
    line <- data.frame(fit_x = seq(min(data$fit_x), max(data$fit_x), length.out = 200))
    line$y <- predict(fit, newdata = line)
    line$x <- if (logarithmic) 10^line$fit_x else line$fit_x
    area_fits[[length(area_fits) + 1L]] <- data.frame(level = level, measure = field, n = nrow(data),
      x_transform = if (logarithmic) 'log10' else 'identity', pearson_r = r,
      slope = unname(coef(fit)[2]), intercept = unname(coef(fit)[1]))
    data$label <- gsub(' (England)', '', data$area_name, fixed = TRUE)
    names_short <- c('Yorkshire and The Humber' = 'Yorkshire & Humber', 'East (England)' = 'East of England', 'Greater London' = 'London')
    selected <- data$area_name %in% names(names_short)
    data$label[selected] <- names_short[data$area_name[selected]]
    write.csv(data, file.path(output_dir, paste0(level, '_', field, '_points.csv')), row.names = FALSE)
    points <- if (level == 'lad') geom_point(aes(colour = ITL1, shape = ITL1), alpha = 1, size = 2.1, stroke = .7) else geom_point(colour = data_colour, alpha = .8, size = 1.8)
    plots[[i]] <- ggplot(data, aes(x, y)) + points +
      geom_line(data = line, colour = if (level == 'lad') '#24343B' else fit_colour, linewidth = .8) +
      annotate('text', x = Inf, y = Inf, label = sprintf('r = %.3f; n = %s', r, number(nrow(data))),
               hjust = 1.05, vjust = 1.3, size = 3, colour = ink) +
      labs(x = paste0('Calls / 10k / day', if (logarithmic) ' (log scale)' else ''), y = rate_labels[i])
    if (level == 'lad') {
      plots[[i]] <- plots[[i]] + scale_colour_manual(values = region_colours, limits = region_names, drop = FALSE) +
        scale_shape_manual(values = region_shapes, limits = region_names, drop = FALSE) +
        theme(legend.position = 'none')
    } else {
      plots[[i]] <- plots[[i]] + ggrepel::geom_text_repel(aes(label = label), seed = 10422,
        size = 2.9, colour = ink, box.padding = .3, point.padding = .18, min.segment.length = 0,
        segment.colour = '#9DA6AA', segment.size = .25, max.overlaps = Inf, max.iter = 20000, max.time = 3) +
        scale_x_continuous(expand = expansion(mult = c(.12, .23))) +
        scale_y_continuous(expand = expansion(mult = c(.12, .2)))
    }
    if (logarithmic) plots[[i]] <- plots[[i]] + log_x()
    save_panels(list(plots[[i]]), paste0(level, '_', c('cancellations', 'delays')[i], '_relationship'), width = 4.2, height = 3.1)
  }
}
write.csv(do.call(rbind, area_fits), file.path(output_dir, 'area_relationship_fits.csv'), row.names = FALSE)
legend_data <- data.frame(x = seq_along(region_names), y = 1, ITL1 = region_names)
legend_plot <- ggplot(legend_data, aes(x, y, colour = ITL1, shape = ITL1)) + geom_point(stroke = .7) +
  scale_colour_manual(values = region_colours, limits = region_names,
    labels = region_labels) +
  scale_shape_manual(values = region_shapes, limits = region_names, labels = region_labels) +
  guides(shape = 'none', colour = guide_legend(nrow = 3, byrow = TRUE, title = NULL,
    override.aes = list(size = 2.6, alpha = 1, shape = unname(region_shapes)))) +
  theme(legend.position = 'bottom', legend.text = element_text(size = 8.5), legend.key.width = grid::unit(.3, 'cm'),
        legend.spacing.x = grid::unit(.1, 'cm'), legend.margin = margin(0, 0, 0, 0))
grDevices::pdf(NULL)
legend_grob <- ggplotGrob(legend_plot)
legend_grob <- legend_grob$grobs[[which(legend_grob$layout$name == 'guide-box-bottom')]]
dev.off()
save_panels(list(legend_grob), 'lad_itl1_legend', width = 8.4, height = .75)

# Join area measures to polygon vertices and draw a map
map_plot <- function(rows, level, column, label, logarithmic = FALSE) {
  d <- vertices[vertices$level == level, ]; stopifnot(all(d$area_code %in% rows$area_code)); d$value <- rows[[column]][match(d$area_code, rows$area_code)]
  p <- ggplot(d, aes(x, y, group = piece, subgroup = ring, fill = value)) +
    geom_polygon(rule = 'evenodd', colour = '#A4ADAA', linewidth = .1) +
    coord_equal() + theme_void(base_size = 10) +
    theme(legend.position = 'bottom', legend.title = element_text(size = 9), legend.text = element_text(size = 8),
          plot.margin = margin(0, 3, 0, 3))
  if (logarithmic) {
    positive <- d[d$value > 0 & !is.na(d$value), ]
    zeros <- d[d$value == 0 & !is.na(d$value), ]
    unavailable <- d[is.na(d$value), ]
    p <- ggplot(positive, aes(x, y, group = piece, subgroup = ring, fill = value)) +
      geom_polygon(rule = 'evenodd', colour = '#A4ADAA', linewidth = .1) +
      geom_polygon(data = zeros, aes(colour = 'No applicable ORR calls'), fill = missing_grey, rule = 'evenodd', linewidth = .1) +
      geom_polygon(data = unavailable, aes(colour = 'Unavailable'), fill = missing_grey, rule = 'evenodd', linewidth = .1) +
      scale_colour_manual(values = c('No applicable ORR calls' = '#A4ADAA', 'Unavailable' = '#A4ADAA'), name = NULL,
                          guide = guide_legend(override.aes = list(fill = missing_grey), order = 2,
                            theme = theme(legend.key.width = grid::unit(2, 'mm'),
                                          legend.key.height = grid::unit(2, 'mm'),
                                          legend.text = element_text(size = 5.5),
                                          legend.margin = margin(0, 0, 0, 0)))) +
      coord_equal() + theme_void(base_size = 10) +
      theme(legend.position = 'right', legend.title = element_text(size = 9), legend.text = element_text(size = 9))
  }
  p + scale_fill_gradientn(colours = viridis, name = label,
                           transform = if (logarithmic) 'log10' else 'identity',
                           breaks = if (logarithmic) log_breaks else scales::breaks_pretty(n = 4),
                           labels = number, na.value = missing_grey, limits = if (logarithmic) NULL else c(0, max(rows[[column]], na.rm = TRUE))) +
    guides(fill = guide_colourbar(barwidth = grid::unit(if (logarithmic) .4 else 1.8, 'in'),
                                  barheight = grid::unit(if (logarithmic) 2 else .12, 'in'),
                                  title.position = 'top'))
}
map_fields <- c('scheduled_calls_per_10000_residents', 'cancellations_pct', 'outside_time_to_3_pct')
map_labels <- c('Calls / 10k / day', 'Cancelled (%)', 'Delayed (>= 3 mins)')
for (level in c('itl1', 'lad')) {
  rows <- switch(level, itl1 = itl, lad = lad)
  maps <- lapply(1:3, function(i) {
    p <- map_plot(rows, level, map_fields[i], map_labels[i], logarithmic = level == 'lad' && i == 1)

    p + theme(legend.position = 'bottom', legend.box = 'vertical', legend.title = element_text(size = 9),
              legend.text = element_text(size = 8)) +
      guides(fill = guide_colourbar(barwidth = grid::unit(1.7, 'in'), barheight = grid::unit(.12, 'in'), title.position = 'top'))
  })
  save_panels(maps, paste0(level, '_three_maps'), width = 8.4, height = 4.2)
  compact <- lapply(maps, function(p) p + theme(legend.title = element_text(size = 8), legend.text = element_text(size = 7)) +
    guides(fill = guide_colourbar(barwidth = grid::unit(1.25, 'in'), barheight = grid::unit(.10, 'in'), title.position = 'top')))
  save_panels(compact, paste0(level, '_standard_page_maps'), width = 4.8, height = 2.55)
}
for (level in names(geographic)) {
  rows <- geographic[[level]]
  maps <- lapply(c('faci', 'fadi'), function(measure) {
    p <- map_plot(rows, level, paste0('geographic_', measure), toupper(measure))

    p + theme(legend.position = 'bottom')
  })
  save_panels(maps, paste0(level, '_index_maps'), width = 8.4, height = if (level == 'lad') 3.5 else 4.5)
  save_panels(maps, paste0(level, '_index_page_maps'), width = 4.8, height = 2.35)
}
message('Rendered geographic figures and tables in R: ', output_dir)

if (file.exists(file.path(source_dir, 'itl2_orr_provision.csv'))) {
  script <- sub('^--file=', '', grep('^--file=', commandArgs(), value = TRUE)[1])
  status <- system2(file.path(R.home('bin'), 'Rscript'),
    shQuote(c('--vanilla', file.path(dirname(script), 'temporal.R'), source_dir, output_dir)))
  if (status != 0) stop('ITL2 temporal rendering failed.')
}

# Render TTWA figures when their CSVs are present
if (file.exists(file.path(source_dir, 'ttwa', 'ttwa_summary.csv'))) {
  script <- sub('^--file=', '', grep('^--file=', commandArgs(), value = TRUE)[1])
  status <- system2(file.path(R.home('bin'), 'Rscript'),
    shQuote(c('--vanilla', file.path(dirname(script), 'ttwa.R'),
              file.path(source_dir, 'ttwa'), file.path(output_dir, 'ttwa'))))
  if (status != 0) stop('TTWA rendering failed.')
}
