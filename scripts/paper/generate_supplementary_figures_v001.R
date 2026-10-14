#!/usr/bin/env Rscript
# Descriptive views of existing results; no re-estimation of primary endpoints.
suppressPackageStartupMessages({library(ggplot2); library(jsonlite); library(patchwork)})
args <- commandArgs(trailingOnly = TRUE)
root <- if (length(args)) normalizePath(args[[1]]) else normalizePath(".")
out <- file.path(root, "paper/figures/supplementary_v001_2026.09.16-r12")
d <- fromJSON(file.path(out, "plot_data_v001.json"))
ink <- "#111111"; blue <- "#A8CADB"; teal <- "#88B5A6"; ochre <- "#C88E66"
theme_set(theme_classic(base_size = 9, base_family = "Arial") + theme(
  text = element_text(colour = ink), axis.text = element_text(colour = ink),
  axis.line = element_line(linewidth = .3), axis.ticks = element_line(linewidth = .3),
  plot.title = element_text(face = "bold", size = 10, margin = margin(b = 8)),
  plot.tag = element_text(face = "bold", size = 12),
  legend.title = element_blank(), legend.text = element_text(size = 8),
  legend.key.width = grid::unit(13, "mm"),
  strip.background = element_blank(), strip.text = element_text(face = "bold", hjust = 0, size = 9),
  panel.spacing = grid::unit(6, "mm"), plot.margin = margin(7, 8, 5, 5)))

save_plot <- function(plot, stem, width, height) {
  if (capabilities("aqua")) {
    grDevices::quartz(type = "pdf", file = file.path(out, paste0(stem, ".pdf")),
                     width = width / 25.4, height = height / 25.4, family = "Arial")
  } else {
    grDevices::cairo_pdf(file.path(out, paste0(stem, ".pdf")),
                        width = width / 25.4, height = height / 25.4, family = "Arial")
  }
  print(plot); invisible(dev.off())
  ggsave(file.path(out, paste0(stem, ".svg")), plot, width = width, height = height,
         units = "mm", device = svglite::svglite, bg = "white")
  ggsave(file.path(out, paste0(stem, ".png")), plot, width = width, height = height,
         units = "mm", dpi = 300, device = ragg::agg_png, bg = "white")
}

panel_names <- c("A  Study 38: tibialis anterior", "B  Gait120: tibialis anterior",
                 "C  Study 38: medial gastrocnemius", "D  Gait120: medial gastrocnemius")
panel_for <- function(x) factor(panel_names[1 + (x$study == "study_120") +
                                           2 * (x$muscle == "medial gastrocnemius")], levels = panel_names)
curves <- d$physiology_curves
curves$panel <- panel_for(curves)
curves$group <- ifelse(curves$pattern_pass, "Participant", curves$subject)
median_curves <- aggregate(value ~ panel + phase, curves, median)
median_curves$group <- "Median"
windows <- data.frame(panel = factor(rep(panel_names, c(2, 2, 1, 1)), levels = panel_names),
                      xmin = c(0,60,0,60,20,20), xmax = c(20,100,20,100,60,60))
failed <- subset(d$physiology_metrics, !pattern_pass)
failed$panel <- panel_for(failed)
failed$phase <- failed$peak_timing_percent_gait_cycle
failed <- merge(failed, curves[, c("panel", "subject", "phase", "value")],
                by = c("panel", "subject", "phase"))
key_order <- c("Participant", "Median", "S013", "S020")
colours <- c(Participant = blue, Median = ink, S013 = ochre, S020 = ochre)
types <- c(Participant = "solid", Median = "solid", S013 = "longdash", S020 = "dotted")
widths <- c(Participant = .45, Median = .85, S013 = .85, S020 = .95)
s1 <- ggplot() +
  geom_rect(data = windows, aes(xmin = xmin, xmax = xmax, ymin = -Inf, ymax = Inf), fill = "#F1F1F1") +
  geom_line(data = subset(curves, pattern_pass), aes(phase, value, group = subject,
             colour = group, linetype = group, linewidth = group), alpha = .9) +
  geom_line(data = median_curves, aes(phase, value, colour = group, linetype = group, linewidth = group)) +
  geom_line(data = subset(curves, !pattern_pass), aes(phase, value, group = subject,
             colour = group, linetype = group, linewidth = group)) +
  geom_point(data = failed, aes(phase, value), shape = 21, fill = "white", colour = ochre, size = 2, stroke = .8) +
  facet_wrap(~panel, ncol = 2) +
  scale_colour_manual(values = colours, breaks = key_order) +
  scale_linetype_manual(values = types, breaks = key_order) +
  scale_linewidth_manual(values = widths, breaks = key_order) +
  scale_x_continuous(breaks = seq(0,100,20), expand = expansion(mult = c(.015,.015))) +
  scale_y_continuous(breaks = c(0,.5,1), limits = c(0,1.03), expand = c(0,0)) +
  labs(x = "Gait cycle (%)", y = "Scaled EMG envelope") +
  theme(legend.position = "bottom", legend.box.margin = margin(t = 4))
save_plot(s1, "figure_s1_participant_envelopes_v001", 190, 143)

wave <- subset(d$boundary_example, method %in% c("reference", "available_sample_RMS", "real_context_0.5s"))
wave$method <- factor(wave$method, levels = c("reference", "available_sample_RMS", "real_context_0.5s"))
pa <- ggplot(wave, aes(phase, value, colour = method, linetype = method)) +
  geom_line(linewidth = .65) +
  geom_point(data = subset(wave, (method == "reference" & phase == 0) |
                            (method == "available_sample_RMS" & phase == 40)), size = 2, show.legend = FALSE) +
  annotate("text", x = c(0,40), y = c(1.08,1.08), label = c("0%", "40%"), size = 2.8, colour = ink) +
  scale_colour_manual(values = c(reference = ink, available_sample_RMS = ochre, real_context_0.5s = teal),
    labels = c("Continuous reference", "Extract, then correct RMS", "Keep 0.5 s surrounding signal")) +
  scale_linetype_manual(values = c(reference = "solid", available_sample_RMS = "solid", real_context_0.5s = "longdash"),
    labels = c("Continuous reference", "Extract, then correct RMS", "Keep 0.5 s surrounding signal")) +
  scale_x_continuous(breaks = seq(0,100,20), expand = expansion(mult = .025)) +
  scale_y_continuous(breaks = c(0,.5,1), limits = c(0,1.12), expand = c(0,0)) +
  labs(title = "A  Boundary-sensitive peak: right lateral gastrocnemius", x = "Interval phase (%)", y = "Peak-scaled envelope") +
  theme(legend.position = "bottom", legend.key.width = grid::unit(10, "mm"), legend.spacing.x = grid::unit(1, "mm"))

intervals <- d$boundary_intervals
methods <- c("legacy", "available_sample_RMS", "real_context_0.125s", "real_context_0.5s")
method_labels <- c("Extract,\nthen process", "Correct\nRMS", "Keep\n0.125 s", "Keep\n0.5 s")
intervals$method <- factor(intervals$method, levels = methods)
record_order <- sort(unique(intervals$record))
intervals$record <- factor(intervals$record, levels = record_order,
                           labels = c("S0088 / V1", "S0088 / V2", "S0088 / V3", "S0172 / V1"))
record_colours <- c("#A8CADB", "#BBAFCB", "#EDCAA5", "#88B5A6")
# Horizontal offsets separate records; numerical observations are never jittered.
intervals$x <- as.numeric(intervals$method) + (as.numeric(intervals$record) - 2.5) * .13 +
               sin((intervals$interval + 1) * 17) * .043
base <- ggplot(intervals, aes(x = x, colour = record, shape = record)) +
  scale_colour_manual(values = record_colours) + scale_shape_manual(values = c(16,17,15,18)) +
  scale_x_continuous(breaks = 1:4, labels = method_labels, limits = c(.55,4.45)) +
  labs(x = NULL) + theme(legend.position = "bottom", legend.key.width = grid::unit(4, "mm"),
                         axis.text.x = element_text(size = 8), plot.margin = margin(6, 8, 5, 5))
pb <- base + geom_point(aes(y = full_cycle_shape_RMSE), size = 1.2, alpha = .65) +
  scale_y_log10(breaks = c(1,1e-3,1e-6,1e-9,1e-12,1e-15),
               labels = scales::label_log()) +
  labs(title = "B  Shape error", y = "RMSE (log scale)")
pc <- base + geom_point(aes(y = global_peak_error_percent), size = 1.2, alpha = .65) +
  scale_y_continuous(breaks = seq(0,40,10), limits = c(-1.5,43)) +
  labs(title = "C  Peak-location error", y = "Difference (percentage points)")
lower <- (pb | pc) + plot_layout(guides = "collect") & theme(legend.position = "bottom")
s2 <- pa / lower + plot_layout(heights = c(1,1.13))
save_plot(s2, "figure_s2_boundary_processing_v001", 190, 172)
message("Wrote Supplementary Figures S1 and S2 (PDF, SVG and 300-dpi PNG).")
