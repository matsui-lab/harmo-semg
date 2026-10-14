#!/usr/bin/env Rscript
# Evidence-backed Figure 3 candidate; preserves the selected manuscript artwork.
suppressPackageStartupMessages({library(ggplot2); library(jsonlite); library(grid)})
args <- commandArgs(trailingOnly = TRUE)
file_arg <- sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE))
root <- normalizePath(file.path(dirname(file_arg), "../.."))
version <- trimws(readLines(file.path(root, "paper/figures/candidates/FIGURE3_VERSION")))
stem <- paste0("figure3_three_panel_", version)
out <- if (length(args)) args[[1]] else file.path(root, "paper/figures/candidates", stem)
dir.create(out, recursive = TRUE, showWarnings = FALSE)
read_json <- function(p) fromJSON(file.path(root, p), simplifyVector = FALSE)
check <- function(ok, msg) if (!isTRUE(ok)) stop(msg, call. = FALSE)
write_csv <- function(d, suffix) write.csv(d, file.path(out, paste0(stem, "_", suffix, ".csv")), row.names = FALSE, na = "")
sources <- c("paper/results/validation/semantic_coverage_comparison.json",
             "paper/results/validation/format_fidelity.json",
             "paper/results/atlas_materialization/atlas.materialization.index.json",
             "paper/results/validation/semantic_overlay_materialization_audit.json")
coverage <- read_json(sources[1]); fidelity <- read_json(sources[2])
atlas <- read_json(sources[3]); overlay <- read_json(sources[4])

# A: count actual property sets, rather than trusting only summary fields.
ids <- c("study31", "study_28", "study_32", "study_33", "study_38", "study_43", "study_120", "study_1")
labels <- c("Controlled-speed walking (31)", "Lifespan + stroke gait (28)",
            "High-density EMG + IMU (32)", "Multi-condition locomotion (33)",
            "Walking + stairs (38)", "Sit-to-walk transitions (43)",
            "Gait120 (120)", "Ramp walking (1)*")
a <- do.call(rbind, lapply(coverage$study_rows, function(r) {
  old <- unlist(r$file_only_properties); combined <- unlist(r$paper_assisted_properties)
  added <- unlist(r$properties_added_by_literature)
  check(!anyDuplicated(old) && !anyDuplicated(combined), "Duplicate study properties")
  check(all(old %in% combined) && setequal(setdiff(combined, old), added), "Coverage set mismatch")
  check(length(old) == r$file_only_property_count && length(combined) == r$paper_assisted_property_count, "Coverage count mismatch")
  data.frame(study_id = r$study_id, file_only = length(old), paper_assisted = length(combined), added = length(added))
}))
a <- a[match(ids, a$study_id), ]; a$label <- labels
check(nrow(a) == 8L && sum(a$file_only) == 24L && sum(a$paper_assisted) == 43L, "A frozen totals changed")
write_csv(a, "A_coverage")
properties <- do.call(rbind, lapply(coverage$study_rows, function(r) data.frame(
  study_id = r$study_id, property = unlist(r$paper_assisted_properties),
  evidence = ifelse(unlist(r$paper_assisted_properties) %in% unlist(r$file_only_properties), "repository", "added_by_publication"))))
write_csv(properties, "A_properties")
examples <- data.frame(study_id = c("study31", "study31", "study_32", "study_32", "study_38", "study_38", "study_38"),
  property = c("processing.emg.bandpass_hz", "processing.emg.rms_window_ms", "processing.emg.raw_to_mv_scale_factor",
               "recording.emg.anatomical_region", "acquisition.emg.bandpass_hz", "recording.emg.channel_count", "recording.emg.laterality_basis"))
check(all(paste(examples$study_id, examples$property) %in% paste(properties$study_id[properties$evidence == "added_by_publication"], properties$property[properties$evidence == "added_by_publication"])), "Unsupported publication example")
a$y <- 61 - (seq_len(nrow(a)) - 1) * 6

# B: passing source-absence controls are not observed numerical matches.
b_rows <- do.call(rbind, lapply(fidelity$comparisons, function(r) data.frame(
  participant = r$subject_id, signal = r$signal, reference = r$reference_format,
  candidate = r$candidate_format, outcome = r$observed_outcome, protocol_pass = r$pass)))
check(nrow(b_rows) == 96L && length(unique(b_rows$participant)) == 16L, "B denominator changed")
b_rows$group <- ifelse(b_rows$candidate == "mat", "ASCII vs MAT", "C3D vs ASCII / MAT")
outcomes <- c("equivalent", "non-equivalent", "source-absent-retained")
groups <- c("ASCII vs MAT", "C3D vs ASCII / MAT")
b <- expand.grid(outcome = outcomes, group = groups, stringsAsFactors = FALSE)
b$n <- mapply(function(g, o) sum(b_rows$group == g & b_rows$outcome == o), b$group, b$outcome)
check(identical(b$n, c(30L, 0L, 2L, 26L, 34L, 4L)), "B outcome counts changed")
check(all(b_rows$protocol_pass[b_rows$group == groups[1]]), "A required control failed")
check(fidelity$denominator$required_equivalence_passed == 32L, "Control accounting mismatch")
write_csv(b_rows, "B_comparison_ledger"); write_csv(b, "B_outcomes")
b$end <- ave(b$n, b$group, FUN = cumsum); b$start <- b$end - b$n
b$y <- ifelse(b$group == groups[1], 45, 25)

# C: sum channel instances over all 12 records and inspect every comparison.
c_records <- do.call(rbind, lapply(atlas$mappings, function(m) do.call(rbind, lapply(m$records, function(r) data.frame(
  study_id = m$study_id, mapping_id = m$mapping_id, recording_id = r$recording_id,
  source = r$source_channel_count, supported = r$qualified_channel_count,
  excluded = length(r$excluded_source_channels), sampled_timepoints = r$sample_count)))))
c_use <- do.call(rbind, lapply(atlas$mappings, function(m) data.frame(
  mapping_id = m$mapping_id, intended_use = m$comparison$intended_use,
  variable = m$comparison$variable, status = m$comparison$status,
  conditions = paste(unlist(m$comparison$conditions), collapse = " | "),
  conditional_releases = sum(vapply(m$release_decisions, function(r) r$status == "conditional", logical(1))))))
check(nrow(c_records) == 12L && nrow(c_use) == 6L && !anyDuplicated(c_records$recording_id), "C record or comparison denominator changed")
check(all(c_records$source == c_records$supported + c_records$excluded), "C channel accounting mismatch")
check(sum(c_records$source) == 160L && sum(c_records$supported) == 143L && sum(c_records$excluded) == 17L, "C frozen totals changed")
check(all(c_records$sampled_timepoints == 100L), "C bounded sampling changed")
check(all(c_use$status == "conditionally_comparable") && all(c_use$intended_use == "activation-timing"), "C use decisions changed")
check(all(c_use$variable == "tibialis-anterior.activation-timing") && sum(c_use$conditional_releases) == 12L, "C variables or releases changed")
check(all(grepl("full recording before scientific inference", c_use$conditions, fixed = TRUE)), "C full-record condition absent")
check(overlay$pass && all(vapply(overlay$mapping_rows, function(r) r$comparison_policy_invariant, logical(1))), "Publication overlay changed comparison policy")
check(all(vapply(overlay$record_rows, function(r) r$normalized_record_invariant, logical(1))), "Publication overlay changed record values")
write_csv(c_records, "C_channel_instances"); write_csv(c_use, "C_use_conditions")

# Pastel plot marks; dark text and rules retain legibility on white.
blue <- "#A8CADB"; ochre <- "#EDCAA5"; grey <- "#D5D9DF"; rule <- "#959DA3"
emphasis <- "#A97936"
pt <- 72.27 / 25.4
txt <- function(x, y, label, size = 8.5, face = "plain", colour = "black", hjust = 0) {
  annotate("text", x = x, y = y, label = label, size = size / pt, family = "Arial",
           fontface = face, colour = colour, hjust = hjust, vjust = 0.5, lineheight = 1.1)
}
line <- function(x, y, xend, yend = y, colour = rule, linewidth = 0.22) {
  annotate("segment", x = x, y = y, xend = xend, yend = yend, colour = colour, linewidth = linewidth)
}
canvas <- function(w, h) ggplot() + coord_cartesian(xlim = c(0, w), ylim = c(0, h), expand = FALSE, clip = "off") +
  theme_void(base_family = "Arial", base_size = 8.5) + theme(plot.margin = margin(0, 0, 0, 0), plot.background = element_rect(fill = "white", colour = NA))
title <- function(letter, label, y) list(txt(2, y, letter, 13, "bold"), txt(10, y, label, 10.5, "bold"))

pA <- canvas(190, 84) + title("A", "Publications added scientific context", 78) +
  txt(10, 69, "Public study", 8.5, "bold") +
  annotate("point", x = 73, y = 69, shape = 21, fill = "white", colour = "#707A82", size = 2.1, stroke = 0.5) +
  txt(76, 69, "File only", 7.5) +
  annotate("point", x = 97, y = 69, shape = 16, colour = blue, size = 2.1) +
  txt(100, 69, "Paper assisted", 7.5) +
  txt(139, 69, "Added information: examples", 8, "bold") +
  line(10, 65, 123) + line(139, 65, 186) +
  txt(10, a$y, a$label, 8) +
  geom_segment(data = a, aes(x = 73 + 6 * file_only, xend = 73 + 6 * paper_assisted, y = y, yend = y), colour = grey, linewidth = 0.5) +
  geom_point(data = a, aes(x = 73 + 6 * file_only, y = y), shape = 21, fill = "white", colour = "#707A82", size = 2.2, stroke = 0.5) +
  geom_point(data = a, aes(x = 73 + 6 * paper_assisted, y = y), colour = blue, size = 2.2) +
  line(73, 13, 121) + line(73 + (0:8) * 6, 13, 73 + (0:8) * 6, 12) +
  txt(73 + (0:8) * 6, 9.7, 0:8, 7.5, hjust = 0.5) +
  txt(97, 4.5, "Covered properties", 8, hjust = 0.5) +
  txt(10, 4.5, "*Biomechanics-only control", 7.5) +
  txt(139, 59, "(31) Processing", 8.5, "bold") +
  txt(139, 53, "Band-pass; RMS window", 8) +
  txt(139, 43, "(32) Measurement", 8.5, "bold") +
  txt(139, 37, "mV scale; electrode region", 8) +
  txt(139, 27, "(38) Acquisition", 8.5, "bold") +
  txt(139, 21, "Filter; channel count; side", 8) +
  line(139, 14, 186) + txt(139, 6, "24 to 43 study-property pairs", 8)

b$colour <- c(blue, ochre, grey)[match(b$outcome, outcomes)]
pB <- canvas(95, 82) + title("B", "Readable does not mean equivalent", 76) +
  txt(10, 68, "Study 31: EMG and marker trajectories", 8) +
  annotate("point", x = c(11, 37, 65), y = 61, colour = c(blue, ochre, grey), size = 2.1, shape = 15) +
  txt(c(14, 40, 68), 61, c("Equivalent", "Discordant", "Absent"), 7.5) +
  txt(10, 54, groups[1], 8.5) + txt(10, 34, groups[2], 8.5) +
  geom_rect(data = b[b$n > 0, ], aes(xmin = 10 + start * 1.2, xmax = 10 + end * 1.2, ymin = y - 2, ymax = y + 2, fill = colour), colour = "white", linewidth = 0.25) +
  scale_fill_identity() + txt(10 + (b$start[b$n > 0] + b$end[b$n > 0]) / 2 * 1.2, b$y[b$n > 0] + 4.2, b$n[b$n > 0], 8, hjust = 0.5) +
  line(10, 17, 86.8) + line(10 + c(0, 16, 32, 48, 64) * 1.2, 17, 10 + c(0, 16, 32, 48, 64) * 1.2, 16) +
  txt(10 + c(0, 16, 32, 48, 64) * 1.2, 13, c(0, 16, 32, 48, 64), 7.5, hjust = 0.5) +
  txt(49, 8, "Representation pairs", 8, hjust = 0.5) +
  txt(10, 2.5, "32/32 controls passed, including 2 absent pairs.", 7.5)

pC <- canvas(95, 82) + title("C", "Use remains conditional", 76) +
  txt(10, 68, "Studies 28 and 33: 12 sampled records", 8) +
  txt(10, 62, "Source-channel instances (n = 160)", 8) +
  annotate("rect", xmin = 10, xmax = 10 + 79 * 143 / 160, ymin = 52, ymax = 56, fill = blue) +
  annotate("rect", xmin = 10 + 79 * 143 / 160, xmax = 89, ymin = 52, ymax = 56, fill = ochre, colour = "white", linewidth = 0.25) +
  txt(c(10 + 79 * 143 / 320, 89 - 79 * 17 / 320), 58.5, c("143", "17"), 8, hjust = 0.5) +
  annotate("point", x = c(11, 66), y = 48, colour = c(blue, ochre), shape = 15, size = 2.1) +
  txt(c(14, 69), 48, c("Anatomy supported", "Excluded"), 7.5) +
  txt(10, 40, "Intended use", 8.5, "bold") + txt(56, 40, "Assessment", 8.5, "bold") +
  line(10, 36.5, 89) + txt(10, 31, "Activation timing", 8.5) +
  txt(56, 31, "Conditional (6/6)", 8.5, colour = emphasis) +
  txt(10, 22.5, "Amplitude pooling", 8.5) + txt(56, 22.5, "Not established", 8.5, colour = emphasis) +
  line(10, 17.5, 89) +
  txt(10, 9.5, "Requires phase alignment, compatible processing,\nand full records before scientific inference.", 7.5)

draw <- function() {
  grid.newpage()
  pushViewport(viewport(layout = grid.layout(2, 2, heights = unit(c(84, 82), "mm"), widths = unit(c(95, 95), "mm"))))
  print(pA, newpage = FALSE, vp = viewport(layout.pos.row = 1, layout.pos.col = 1:2))
  print(pB, newpage = FALSE, vp = viewport(layout.pos.row = 2, layout.pos.col = 1))
  print(pC, newpage = FALSE, vp = viewport(layout.pos.row = 2, layout.pos.col = 2))
  popViewport()
}
pdf_path <- file.path(out, paste0(stem, ".pdf"))
if (Sys.info()[["sysname"]] == "Darwin") {
  grDevices::quartz(type = "pdf", file = pdf_path, width = 190 / 25.4, height = 166 / 25.4,
                   family = "Arial", title = "Figure 3. Scientific context and limits on reuse")
} else grDevices::cairo_pdf(pdf_path, width = 190 / 25.4, height = 166 / 25.4, family = "Arial", onefile = TRUE)
draw(); invisible(dev.off())
svglite::svglite(file.path(out, paste0(stem, ".svg")), width = 190 / 25.4, height = 166 / 25.4, bg = "white")
draw(); invisible(dev.off())
ragg::agg_png(file.path(out, paste0(stem, ".png")), width = 190, height = 166, units = "mm", res = 600, background = "white")
draw(); invisible(dev.off())
writeLines(trimws(capture.output(sessionInfo()), which = "right"), file.path(out, paste0(stem, "_session.txt")))
sources <- c(sources, "scripts/paper/generate_figure3_ggplot.R", "paper/figures/candidates/FIGURE3_VERSION",
             paste0("paper/figures/candidates/", stem, "/", stem, "_caption.md"))
manifest <- list(figure = 3, status = "candidate_not_adopted", version = version,
  graphic_engine = paste("ggplot2", packageVersion("ggplot2")), width_mm = 190, height_mm = 166,
  raster_dpi = 600, minimum_text_pt = 7.5, normal_text_pt = 8.5, title_text_pt = 10.5, font = "Arial",
  palette = list(supported = blue, restricted = ochre, absent = grey, emphasis_text = emphasis),
  checks = list(file_only_pairs = sum(a$file_only), paper_assisted_pairs = sum(a$paper_assisted),
    required_controls_passed = 32, required_controls_numerically_equivalent = 30, required_controls_source_absent = 2,
    c3d_equivalent = 26, c3d_discordant = 34, c3d_source_absent = 4,
    source_channel_instances = sum(c_records$source), supported = sum(c_records$supported), excluded = sum(c_records$excluded),
    comparisons = nrow(c_use), conditional_releases = sum(c_use$conditional_releases)),
  interpretation_note = "Protocol-passing controls include retained source absence. Amplitude pooling is not an evaluated second use endpoint.",
  sources = lapply(sources, function(p) list(path = p, sha256 = digest::digest(file = file.path(root, p), algo = "sha256"))))
write_json(manifest, file.path(out, paste0(stem, "_evidence.json")), pretty = TRUE, auto_unbox = TRUE)
cat("Created Figure 3 candidate:", out, "\n")
