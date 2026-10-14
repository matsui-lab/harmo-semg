#!/usr/bin/env Rscript
suppressPackageStartupMessages({
  library(ggplot2)
  library(jsonlite)
  library(patchwork)
})
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 2, !dir.exists(args[2]))
data <- fromJSON(args[1], simplifyVector = FALSE)
stopifnot(data$status == "complete", length(data$participants) == 20)
conditions <- c("fixed_window", "observed_samples")
condition_labels <- c("Fixed-window denominator", "Observed-sample denominator")
palette <- c("#75818F", "#5C9D93")
names(palette) <- condition_labels
theme_set(theme_classic(base_size = 11) + theme(
  text = element_text(colour = "black"),
  axis.text = element_text(colour = "black"),
  plot.title = element_text(size = 11, face = "bold", margin = margin(b = 9)),
  legend.title = element_blank(), legend.position = "bottom",
  strip.background = element_blank(), strip.text = element_text(colour = "black"),
  plot.margin = margin(8, 10, 8, 8)
))
example <- Filter(function(r) r$participant == "1" && r$muscle == "tibialis anterior", data$participants)[[1]]
curves <- do.call(rbind, lapply(seq_along(conditions), function(k) data.frame(
  phase = 0:100, envelope = unlist(example$curves[[conditions[k]]]),
  condition = factor(condition_labels[k], levels = condition_labels)
)))
peaks <- do.call(rbind, lapply(seq_along(conditions), function(k) {
  phase <- example$metrics[[conditions[k]]]$peak_timing_percent_gait_cycle
  data.frame(phase = phase, envelope = example$curves[[conditions[k]]][[phase + 1]],
             condition = factor(condition_labels[k], levels = condition_labels))
}))
a <- ggplot(curves, aes(phase, envelope, colour = condition)) +
  geom_line(linewidth = 0.8) + geom_point(data = peaks, size = 2.4) +
  scale_colour_manual(values = palette) +
  scale_x_continuous(breaks = c(0, 25, 50, 75, 100), expand = expansion(mult = c(0.01, 0.02))) +
  scale_y_continuous(limits = c(0, 1.04), breaks = seq(0, 1, 0.25), expand = expansion(mult = c(0, 0))) +
  labs(title = "A  Illustrative waveform", subtitle = "Lencioni participant 1 · tibialis anterior",
       x = "Gait cycle (%)", y = "Within-cycle-scaled envelope") +
  guides(colour = guide_legend(ncol = 1))
participant_peaks <- do.call(rbind, lapply(data$participants, function(r) data.frame(
  participant = as.integer(r$participant), change = r$peak_change_pp,
  muscle = ifelse(r$muscle == "tibialis anterior", "Tibialis anterior", "Medial gastrocnemius")
)))
participant_peaks$muscle <- factor(participant_peaks$muscle, levels = c("Tibialis anterior", "Medial gastrocnemius"))
b <- ggplot(participant_peaks, aes(change, participant)) +
  geom_vline(xintercept = 0, colour = "grey75", linewidth = 0.4) +
  geom_point(size = 2, colour = "#3C625C") +
  facet_wrap(~muscle, ncol = 1) +
  scale_y_reverse(breaks = c(1, 5, 10)) +
  scale_x_continuous(breaks = c(-5, 0, 10, 20, 30, 40)) +
  labs(title = "B  Peak change in all participants", x = "Alternative − existing (pp)", y = "Participant")
effects <- do.call(rbind, lapply(seq_along(data$muscles), function(k) data.frame(
  muscle = ifelse(k == 1, "Tibialis\nanterior", "Medial\ngastrocnemius"),
  effect = data$summary$change_in_study_difference[[k]],
  low = data$summary$change_in_study_difference_ci[[k]][[1]],
  high = data$summary$change_in_study_difference_ci[[k]][[2]]
)))
effects$muscle <- factor(effects$muscle, levels = rev(c("Tibialis\nanterior", "Medial\ngastrocnemius")))
c <- ggplot(effects, aes(effect, muscle)) +
  geom_vline(xintercept = 0, colour = "grey65", linetype = "dashed", linewidth = 0.4) +
  geom_segment(aes(x = low, xend = high, yend = muscle), linewidth = 0.65) +
  geom_point(size = 2.4) +
  scale_x_continuous(breaks = c(0, 0.25, 0.5, 0.75, 1), limits = c(-0.1, 1)) +
  labs(title = "C  Change in study difference", subtitle = "Activity centroid · 95% bootstrap intervals",
       x = "Change in Gait120 − Lencioni difference (pp)", y = NULL)
figure <- a + b + c + plot_layout(widths = c(1.2, 1.05, 1.15))
dir.create(args[2], recursive = TRUE)
ggsave(file.path(args[2], "rms_sensitivity_review.png"), figure,
       width = 12.8, height = 4.8, dpi = 180, bg = "white")
ggsave(file.path(args[2], "rms_sensitivity_review.svg"), figure,
       width = 12.8, height = 4.8, bg = "white")
capture.output(sessionInfo(), file = file.path(args[2], "R_session.txt"))
