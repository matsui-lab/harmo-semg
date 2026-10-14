#!/usr/bin/env Rscript
# Reuse validated participant curves and bootstrap estimates without refitting.
suppressPackageStartupMessages({library(ggplot2); library(jsonlite); library(patchwork)})
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 3, !dir.exists(args[3]))
activity <- fromJSON(args[1], simplifyVector = FALSE)
processing <- fromJSON(args[2], simplifyVector = FALSE)
stopifnot(activity$status == "complete", processing$status == "complete",
          activity$checks$participants == 20, length(activity$participants) == 120,
          identical(unlist(activity$conditions), c("existing", "common_trial", "common_cycle")))
muscles <- c("Tibialis anterior", "Medial gastrocnemius")
study_names <- c(study_38 = "Lencioni et al. (2019)", study_120 = "Gait120")
condition_names <- c(existing = "Released", common_trial = "Process then cut",
                     common_cycle = "Cut then process")
study_colours <- setNames(c("#77A6B6", "#CB9F87"), unname(study_names))
condition_colours <- setNames(c("#9CA6B3", "#88B09F", "#C79678"), unname(condition_names))
muscle_label <- function(x) {
  stopifnot(x %in% c("tibialis anterior", "medial gastrocnemius"))
  factor(muscles[match(x, c("tibialis anterior", "medial gastrocnemius"))], levels = muscles)
}
study_label <- function(x) {
  stopifnot(x %in% names(study_names))
  factor(study_names[[x]], levels = unname(study_names))
}
theme_set(theme_classic(base_size = 12, base_family = "sans") + theme(
  text = element_text(colour = "black"), axis.text = element_text(colour = "black"),
  plot.title = element_text(face = "bold", size = 14, margin = margin(b = 5)),
  plot.subtitle = element_text(size = 10.5, margin = margin(b = 8)),
  strip.background = element_blank(), strip.text = element_text(face = "bold", size = 11.5),
  panel.spacing.x = grid::unit(2, "lines"), legend.position = "bottom",
  legend.title = element_blank(), legend.key.width = grid::unit(1.8, "lines"),
  legend.margin = margin(t = 0, b = 0), plot.margin = margin(7, 12, 7, 8)))

waves <- do.call(rbind, lapply(activity$participants, function(r) {
  stopifnot(length(r$representative_curve) == 101, r$n_cycles >= 5)
  data.frame(study = study_label(r$study), muscle = muscle_label(r$muscle),
    participant = r$participant,
    condition = factor(condition_names[[r$condition]], levels = unname(condition_names)),
    phase = 0:100, value = unlist(r$representative_curve))
}))
stopifnot(nrow(waves) == 120 * 101, all(is.finite(waves$value)),
          all(waves$value >= 0 & waves$value <= 1 + 1e-12))

# First take each participant's representative curve, then summarize participants.
summarize_curves <- function(dat, groups) {
  parts <- split(dat, interaction(dat[groups], drop = TRUE))
  ans <- do.call(rbind, lapply(parts, function(z) {
    stopifnot(nrow(z) == 10, length(unique(z$participant)) == 10)
    cbind(z[1, groups, drop = FALSE], data.frame(
      value = median(z$value), low = unname(quantile(z$value, .25)),
      high = unname(quantile(z$value, .75))))
  }))
  ans[do.call(order, ans[groups]), ]
}
gait <- summarize_curves(subset(waves, study == "Gait120"), c("muscle", "condition", "phase"))
panel3a <- ggplot(gait, aes(phase, value, colour = condition, linetype = condition)) +
  geom_line(linewidth = .9) + facet_wrap(~muscle, nrow = 1) +
  scale_colour_manual(values = condition_colours) +
  scale_linetype_manual(values = c("solid", "longdash", "dotted")) +
  scale_x_continuous(breaks = c(0,20,40,60,80,100), limits = c(0,100), expand = expansion(mult = .005)) +
  scale_y_continuous(limits = c(0,1.02), breaks = c(0,.5,1), expand = expansion(mult = .01)) +
  labs(title = "A  Processing changes the activity profile",
       subtitle = "Gait120 · median of 10 participant profiles",
       x = "Gait cycle (%)", y = "Scaled EMG envelope")

effects <- do.call(rbind, lapply(1:2, function(m) do.call(rbind, lapply(1:3, function(k) {
  data.frame(muscle = factor(muscles[m], levels = muscles),
    condition = factor(unname(condition_names[k]), levels = rev(unname(condition_names))),
    value = processing$summary$study_differences[[k]][[m]],
    low = processing$summary$study_difference_ci[[k]][[m]][[1]],
    high = processing$summary$study_difference_ci[[k]][[m]][[2]])
}))))
effects$label <- sprintf("%+.2f", effects$value)
panel3b <- ggplot(effects, aes(value, condition, colour = condition)) +
  geom_vline(xintercept = 0, colour = "grey65", linetype = "dashed", linewidth = .4) +
  geom_segment(aes(x = low, xend = high, yend = condition), linewidth = .9) +
  geom_point(size = 2.8) +
  geom_text(aes(label = label), colour = "black", nudge_y = .23, size = 3.4) +
  facet_wrap(~muscle, nrow = 1) + scale_colour_manual(values = condition_colours, guide = "none") +
  scale_x_continuous(limits = c(-9,7), breaks = c(-8,-4,0,4), expand = expansion(mult = .02)) +
  scale_y_discrete(expand = expansion(add = .55)) +
  labs(title = "B  Between-study timing differences depend on processing",
       subtitle = "Gait120 processing varies; Lencioni processing remains fixed",
       x = "Gait120 − Lencioni centroid difference (gait-cycle percentage points)", y = NULL)

contrast_keys <- c("cycle_minus_existing", "cycle_minus_trial")
contrast_labels <- c("Cut then process\nminus released", "Cut then process\nminus process then cut")
changes <- do.call(rbind, lapply(1:2, function(m) do.call(rbind, lapply(1:2, function(k) {
  z <- processing$summary$contrasts[[contrast_keys[k]]]
  data.frame(muscle = factor(muscles[m], levels = muscles), contrast_key = contrast_keys[k],
    contrast = factor(contrast_labels[k], levels = rev(contrast_labels)),
    value = z$change_in_study_difference[[m]], low = z$change_ci[[m]][[1]], high = z$change_ci[[m]][[2]])
}))))
changes$label <- sprintf("%+.2f", changes$value)
panel3c <- ggplot(changes, aes(value, contrast)) +
  geom_vline(xintercept = 0, colour = "grey65", linetype = "dashed", linewidth = .4) +
  geom_segment(aes(x = low, xend = high, yend = contrast), linewidth = .9, colour = "#80929D") +
  geom_point(size = 2.8, shape = 21, fill = "#BAC9D0", colour = "black", stroke = .4) +
  geom_text(aes(label = label), nudge_y = .2, size = 3.4) +
  facet_wrap(~muscle, nrow = 1) +
  scale_x_continuous(limits = c(-7,2), breaks = c(-6,-4,-2,0,2), expand = expansion(mult = .02)) +
  scale_y_discrete(expand = expansion(add = .5)) +
  labs(title = "C  How much did the study difference change?",
       subtitle = "Within-participant pairing retained · points and 95% bootstrap intervals in B and C",
       x = "Change in centroid difference (gait-cycle percentage points)", y = NULL)
figure3 <- (panel3a / panel3b / panel3c) + plot_layout(heights = c(1.25,1,.95))

selected <- Filter(function(r) r$condition == "common_cycle", activity$participants)
common <- subset(waves, condition == "Cut then process")
profiles <- summarize_curves(common, c("study", "muscle", "phase"))
panel4a <- ggplot(profiles, aes(phase, value, colour = study, fill = study, linetype = study)) +
  geom_vline(xintercept = c(20,60), colour = "grey78", linetype = "dotted", linewidth = .4) +
  geom_ribbon(aes(ymin = low, ymax = high), alpha = .16, colour = NA, linetype = "solid") +
  geom_line(linewidth = 1) + facet_wrap(~muscle, nrow = 1) +
  scale_colour_manual(values = study_colours) + scale_fill_manual(values = study_colours) +
  scale_linetype_manual(values = c("solid", "longdash")) +
  scale_x_continuous(breaks = c(0,20,40,60,80,100), limits = c(0,100), expand = expansion(mult = .005)) +
  scale_y_continuous(limits = c(0,1.02), breaks = c(0,.5,1), expand = expansion(mult = .01)) +
  labs(title = "A  Muscle activity profiles from two public studies",
       subtitle = "Study medians and between-participant interquartile ranges · 10 participants per study",
       x = "Gait cycle (%)", y = "Scaled EMG envelope")

share <- do.call(rbind, lapply(selected, function(r) {
  peers <- sort(unique(vapply(Filter(function(z) z$study == r$study, selected), function(z) z$participant, "")))
  offset <- (match(r$participant, peers) - 5.5) * .017
  group_offset <- if (r$study == "study_38") -.19 else .19
  do.call(rbind, lapply(1:3, function(i) data.frame(
    study = study_label(r$study), muscle = muscle_label(r$muscle), participant = r$participant,
    region = i, center = i + group_offset, x = i + group_offset + offset,
    value = 100 * r$area_share_median[[i]], low = 100 * r$area_share_q25[[i]],
    high = 100 * r$area_share_q75[[i]])))
}))
share_summary <- aggregate(value ~ study + muscle + region + center, share, median)
stopifnot(nrow(share) == 120, nrow(share_summary) == 12,
          all(share$low <= share$value), all(share$value <= share$high))
panel4b <- ggplot(share, aes(x, value, colour = study, shape = study)) +
  geom_linerange(aes(ymin = low, ymax = high), alpha = .55, linewidth = .4) +
  geom_point(size = 1.9, alpha = .95) +
  geom_segment(data = share_summary, aes(x = center - .11, xend = center + .11,
    y = value, yend = value), inherit.aes = FALSE, colour = "black", linewidth = 1) +
  facet_wrap(~muscle, nrow = 1) + scale_colour_manual(values = study_colours) +
  scale_shape_manual(values = c(16,17)) +
  scale_x_continuous(breaks = 1:3, labels = c("0–20%", "20–60%", "60–100%"), limits = c(.5,3.5)) +
  scale_y_continuous(limits = c(0,100), breaks = c(0,25,50,75,100), expand = expansion(mult = .02)) +
  labs(title = "B  Activity is concentrated in different parts of the cycle",
       subtitle = "Points: participant medians · vertical lines: cycle interquartile ranges · black bars: study medians",
       x = "Cycle interval", y = "Share of total envelope area (%)") +
  guides(colour = guide_legend(override.aes = list(alpha = 1, linewidth = 0)))
figure4 <- (panel4a / panel4b) + plot_layout(heights = c(1,1.05))

# Preserve all individuals in a companion figure, including atypical profiles.
all_profiles <- ggplot(common, aes(phase, value, group = participant, colour = study)) +
  geom_line(linewidth = .6) +
  facet_wrap(vars(study, participant, muscle), ncol = 4) +
  scale_colour_manual(values = study_colours, guide = "none") +
  scale_x_continuous(breaks = c(0,50,100)) + scale_y_continuous(limits = c(0,1.02), breaks = c(0,.5,1)) +
  labs(title = "All participant activity profiles", subtitle = "Common cycle processing · no participant omitted",
       x = "Gait cycle (%)", y = "Scaled EMG envelope") + theme(strip.text = element_text(size = 8))

dir.create(args[3], recursive = TRUE)
for (name in c("figure3_processing", "figure4_activity")) {
  fig <- if (name == "figure3_processing") figure3 else figure4
  ggsave(file.path(args[3], paste0(name, ".png")), fig, width = 12, height = 9, dpi = 240, bg = "white")
  ggsave(file.path(args[3], paste0(name, ".svg")), fig, width = 12, height = 9, bg = "white")
}
ggsave(file.path(args[3], "all_participant_profiles.png"), all_profiles, width = 12, height = 17, dpi = 180, bg = "white")
write_json(list(inputs = args[1:2], participants = 20, participant_muscle_profiles = 40,
  figure3_study_differences = effects, figure3_changes = changes,
  figure4_area_medians = share_summary, figure4_waveforms = profiles,
  bootstrap = processing$bootstrap, intervals = "Existing paired participant-bootstrap intervals; not differences between CI endpoints",
  curve_bands = "Pointwise between-participant IQR; not confidence intervals"),
  file.path(args[3], "plot_data.json"), pretty = TRUE, auto_unbox = TRUE, digits = NA)
capture.output(sessionInfo(), file = file.path(args[3], "R_session.txt"))
message("Saved Figures 3 and 4, all 40 participant-muscle profiles, and plotted source values.")
