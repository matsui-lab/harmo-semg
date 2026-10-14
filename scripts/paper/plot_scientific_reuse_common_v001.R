#!/usr/bin/env Rscript
suppressPackageStartupMessages({library(ggplot2); library(jsonlite); library(patchwork)})
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 2, !dir.exists(args[2]))
d <- fromJSON(args[1], simplifyVector = FALSE)
stopifnot(d$status == "complete", length(d$gait120_participants) == 20)
conditions <- unlist(d$conditions)
labels <- c("Existing", "Common:\nwhole trial", "Common:\ncycle")
colours <- c("#7F8996", "#88ACA4", "#C09F7B")
muscle_label <- function(m) ifelse(m == "tibialis anterior", "Tibialis anterior", "Medial gastrocnemius")
theme_set(theme_classic(base_size = 11) + theme(
  text = element_text(colour = "black"), axis.text = element_text(colour = "black"),
  plot.title = element_text(face = "bold", size = 12, margin = margin(b = 7)),
  legend.title = element_blank(), legend.position = "bottom",
  strip.background = element_blank(), strip.text = element_text(colour = "black", face = "bold"),
  plot.margin = margin(10, 12, 10, 10)
))
muscle_levels <- c("Tibialis anterior", "Medial gastrocnemius")
effect <- do.call(rbind, lapply(1:2, function(m) do.call(rbind, lapply(1:3, function(k) data.frame(
  muscle = factor(muscle_label(d$muscles[[m]]), levels = muscle_levels),
  condition = factor(labels[k], levels = rev(labels)),
  delta = d$summary$study_differences[[k]][[m]],
  low = d$summary$study_difference_ci[[k]][[m]][[1]],
  high = d$summary$study_difference_ci[[k]][[m]][[2]], k = factor(k)
)))))
a <- ggplot(effect, aes(delta, condition, colour = k)) +
  geom_vline(xintercept = 0, colour = "grey65", linetype = "dashed", linewidth = 0.4) +
  geom_segment(aes(x = low, xend = high, yend = condition), linewidth = 0.75) +
  geom_point(size = 2.5) + facet_wrap(~muscle, ncol = 1) +
  scale_colour_manual(values = colours, guide = "none") +
  scale_x_continuous(breaks = c(-8, -4, 0, 4, 8)) +
  labs(title = "A  Between-study timing differences", subtitle = "Study medians with 95% bootstrap intervals",
       x = "Gait120 − Lencioni centroid difference (pp)", y = NULL)
points <- do.call(rbind, lapply(d$gait120_participants, function(r) do.call(rbind, lapply(1:3, function(k) data.frame(
  participant = r$participant, muscle = factor(muscle_label(r$muscle), levels = muscle_levels),
  condition = factor(labels[k], levels = labels),
  centroid = r$metrics[[conditions[k]]]$activation_centroid_percent_gait_cycle, k = factor(k)
)))))
b <- ggplot(points, aes(condition, centroid, group = participant)) +
  geom_line(colour = "grey72", linewidth = 0.5) +
  geom_point(aes(colour = k), size = 2) +
  facet_wrap(~muscle, ncol = 1) +
  scale_colour_manual(values = colours, guide = "none") +
  labs(title = "B  Same participants across conditions", subtitle = "Gait120 · 10 participants per muscle",
       x = NULL, y = "Activity centroid (% gait cycle)")
overview <- a + b + plot_layout(widths = c(1.2, 1))
curves <- do.call(rbind, lapply(d$gait120_participants, function(r) do.call(rbind, lapply(1:3, function(k) data.frame(
  panel = paste(r$participant, ifelse(r$muscle == "tibialis anterior", "TA", "MG")),
  phase = 0:100, envelope = unlist(r$curves[[conditions[k]]]),
  condition = factor(labels[k], levels = labels)
)))))
curveplot <- ggplot(curves, aes(phase, envelope, colour = condition)) +
  geom_line(linewidth = 0.5) + facet_wrap(~panel, ncol = 5) +
  scale_colour_manual(values = colours) +
  scale_x_continuous(breaks = c(0, 50, 100)) +
  scale_y_continuous(limits = c(0, 1.04), breaks = c(0, 0.5, 1)) +
  labs(title = "Gait120: all participant-muscle curves", x = "Gait cycle (%)", y = "Within-cycle-scaled envelope")
dir.create(args[2], recursive = TRUE)
ggsave(file.path(args[2], "common_processing_review.png"), overview, width = 10.5, height = 6.2, dpi = 180, bg = "white")
ggsave(file.path(args[2], "common_processing_review.svg"), overview, width = 10.5, height = 6.2, bg = "white")
ggsave(file.path(args[2], "all_participant_waveforms.png"), curveplot, width = 12, height = 8.5, dpi = 180, bg = "white")
capture.output(sessionInfo(), file = file.path(args[2], "R_session.txt"))
