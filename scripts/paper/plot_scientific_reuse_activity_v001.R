#!/usr/bin/env Rscript
suppressPackageStartupMessages({library(ggplot2); library(jsonlite); library(patchwork)})
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 3, !dir.exists(args[3]))
d <- fromJSON(args[1], simplifyVector = FALSE)
old <- fromJSON(args[2], simplifyVector = FALSE)
stopifnot(d$status == "complete", d$checks$participants == 20,
          length(d$participants) == 120)
muscles <- c("Tibialis anterior", "Medial gastrocnemius")
ml <- function(x) factor(ifelse(x == "tibialis anterior", muscles[1], muscles[2]), levels = muscles)
sl <- function(x) factor(ifelse(x == "study_38", "Lencioni", "Gait120"), levels = c("Lencioni", "Gait120"))
cols <- c("Lencioni" = "#7BA4B0", "Gait120" = "#C19D88")
cl <- c("Existing", "Common: whole trial", "Common: cycle")
cc <- c("#8999A8", "#94B8A7", "#CFB097")
theme_set(theme_classic(base_size = 11) + theme(
 text = element_text(colour = "black"), axis.text = element_text(colour = "black"),
 plot.title = element_text(face = "bold", size = 12),
 plot.subtitle = element_text(size = 9.5, margin = margin(b = 7)),
 strip.background = element_blank(), strip.text = element_text(face = "bold"),
 panel.spacing.x = grid::unit(1.6, "lines"),
 legend.position = "bottom", legend.title = element_blank(),
 plot.margin = margin(8, 12, 8, 8)))
selected <- Filter(function(r) r$condition == "common_cycle", d$participants)
waves <- do.call(rbind, lapply(selected, function(r) data.frame(
 study = sl(r$study), muscle = ml(r$muscle), participant = r$participant,
 phase = 0:100, value = unlist(r$representative_curve))))
a <- ggplot(waves, aes(phase, value)) +
 geom_vline(xintercept = c(20,60), colour = "grey80", linetype = "dotted", linewidth = .35) +
 geom_line(aes(group = participant, colour = study), alpha = .45, linewidth = .4) +
 stat_summary(aes(group = study), fun = median, geom = "line", colour = "black", linewidth = .8) +
 facet_grid(study ~ muscle) + scale_colour_manual(values = cols, guide = "none") +
 scale_x_continuous(breaks = c(0,20,60,100), limits = c(0,100), expand = expansion(mult = .01)) +
 scale_y_continuous(limits = c(0,1.03), breaks = c(0,.5,1), expand = expansion(mult = c(0,.01))) +
 labs(title = "A  Activity profiles from both studies",
      subtitle = "Common cycle processing · all 20 participants",
      x = "Gait cycle (%)", y = "Within-cycle-scaled envelope")
regions <- c("0–20%", "20–60%", "60–100%")
share <- do.call(rbind, lapply(selected, function(r) {
 sid <- if (r$study == "study_38") as.integer(r$participant) else as.integer(sub("S0","",r$participant)) - 10
 do.call(rbind, lapply(1:3, function(i) data.frame(
  study = sl(r$study), muscle = ml(r$muscle), participant = r$participant,
  region = i, x = i + ifelse(r$study == "study_38", -.16, .16) + (sid - 5.5) * .016,
  value = 100*r$area_share_median[[i]], low = 100*r$area_share_q25[[i]],
  high = 100*r$area_share_q75[[i]])))
}))
b <- ggplot(share, aes(x, value, colour = study)) +
 geom_linerange(aes(ymin = low, ymax = high), alpha = .45, linewidth = .35) +
 geom_point(size = 1.6, alpha = .9) +
 facet_wrap(~muscle, ncol = 1) + scale_colour_manual(values = cols) +
 scale_x_continuous(breaks = 1:3, labels = regions, limits = c(.5,3.5)) +
 scale_y_continuous(limits = c(0,100), breaks = c(0,25,50,75,100), expand = expansion(mult = .01)) +
 labs(title = "B  Activity distributed across the cycle",
      subtitle = "Participant medians and within-participant interquartile ranges",
      x = "Cycle interval", y = "Share of total envelope area (%)") +
 guides(colour = guide_legend(override.aes = list(alpha = 1, linewidth = 0)))
effects <- do.call(rbind, lapply(1:2, function(m) do.call(rbind, lapply(1:3, function(k) data.frame(
 muscle = factor(muscles[m], levels = muscles), condition = factor(cl[k], levels = rev(cl)),
 value = old$summary$study_differences[[k]][[m]],
 low = old$summary$study_difference_ci[[k]][[m]][[1]],
 high = old$summary$study_difference_ci[[k]][[m]][[2]], k = factor(k)
)))))
c <- ggplot(effects, aes(value, condition, colour = k)) +
 geom_vline(xintercept = 0, colour = "grey60", linetype = "dashed", linewidth = .4) +
 geom_segment(aes(x = low, xend = high, yend = condition), linewidth = .8) +
 geom_point(size = 2.6) + facet_wrap(~muscle, nrow = 1) +
 scale_colour_manual(values = cc, guide = "none") +
 scale_x_continuous(breaks = c(-8,-4,0,4,8)) +
 labs(title = "C  Study differences depend on processing",
      subtitle = "Gait120 processing varies; Lencioni remains fixed · 95% participant-bootstrap intervals",
      x = "Gait120 − Lencioni centroid difference (gait-cycle percentage points)", y = NULL)
fig <- ((a | b) / c) + plot_layout(heights = c(1.75,1), widths = c(1,1))
allwaves <- do.call(rbind, lapply(d$participants, function(r) data.frame(
 study = sl(r$study), muscle = ml(r$muscle), participant = r$participant,
 condition = factor(cl[match(r$condition, unlist(d$conditions))], levels = cl),
 phase = 0:100, value = unlist(r$representative_curve))))
allwaves$panel <- paste(allwaves$study, allwaves$participant,
                       ifelse(allwaves$muscle == muscles[1], "TA", "MG"))
s <- ggplot(allwaves, aes(phase,value,colour=condition)) +
 geom_line(linewidth=.5) + facet_wrap(~panel,ncol=4) +
 scale_colour_manual(values=cc) + scale_x_continuous(breaks=c(0,50,100)) +
 scale_y_continuous(limits=c(0,1.04),breaks=c(0,.5,1)) +
 labs(x="Gait cycle (%)", y="Within-cycle-scaled envelope",
      title="All participant profiles across processing conditions",
      subtitle="Lencioni is identical across conditions; overlapping lines are not independent repeats.")
dir.create(args[3], recursive = TRUE)
ggsave(file.path(args[3],"figure3_activity_review.png"),fig,width=12,height=9.4,dpi=200,bg="white")
ggsave(file.path(args[3],"figure3_activity_review.svg"),fig,width=12,height=9.4,bg="white")
ggsave(file.path(args[3],"all_participant_profiles.png"),s,width=12,height=17,dpi=180,bg="white")
capture.output(sessionInfo(),file=file.path(args[3],"R_session.txt"))
