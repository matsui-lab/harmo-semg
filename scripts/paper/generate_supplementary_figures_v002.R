#!/usr/bin/env Rscript
# Descriptive views of existing results; no re-estimation of primary endpoints.
suppressPackageStartupMessages({library(ggplot2); library(jsonlite); library(patchwork)})
args <- commandArgs(trailingOnly = TRUE)
root <- if (length(args)) normalizePath(args[[1]]) else normalizePath(".")
out <- file.path(root, "paper/figures/supplementary_v002_2026.09.26-r1")
dir.create(out, recursive=TRUE, showWarnings=FALSE)
d <- fromJSON(file.path(root, "paper/figures/supplementary_v001_2026.09.16-r12/plot_data_v001.json"))
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

panel_names <- c("A  Lencioni 2019: tibialis anterior", "B  Gait120: tibialis anterior",
                 "C  Lencioni 2019: medial gastrocnemius", "D  Gait120: medial gastrocnemius")
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
save_plot(s1, "figure_s1_participant_envelopes_v002", 190, 143)
