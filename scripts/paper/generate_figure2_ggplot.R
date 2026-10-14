#!/usr/bin/env Rscript
# Evidence-backed Figure 2 candidate. Does not replace the manuscript artwork.
suppressPackageStartupMessages({
  library(ggplot2)
  library(jsonlite)
  library(grid)
})
args <- commandArgs(trailingOnly = TRUE)
file_arg <- sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE))
root <- normalizePath(file.path(dirname(file_arg), "../.."))
version <- trimws(readLines(file.path(root, "paper/figures/candidates/FIGURE2_VERSION")))
stem <- paste0("figure2_three_panel_", version)
out <- if (length(args)) args[[1]] else file.path(root, "paper/figures/candidates", stem)
dir.create(out, recursive = TRUE, showWarnings = FALSE)
read_json <- function(path) fromJSON(file.path(root, path), simplifyVector = FALSE)
check <- function(condition, message) if (!isTRUE(condition)) stop(message, call. = FALSE)
write_csv <- function(x, suffix) write.csv(x, file.path(out, paste0(stem, "_", suffix, ".csv")), row.names = FALSE, na = "")
source_paths <- c(
  "paper/tables/table1_public_studies.csv",
  "paper/results/validation/mapping_outcomes.csv",
  "paper/results/validation/corpus_execution.json",
  "paper/results/validation/reproducibility.json",
  "paper/results/validation/adapter_metrics.json",
  "paper/results/validation/ultra_mocap_trial_manifest.json"
)
mapping <- read.csv(file.path(root, source_paths[[2]]), stringsAsFactors = FALSE)
studies <- read.csv(file.path(root, source_paths[[1]]), stringsAsFactors = FALSE)
corpus <- read_json(source_paths[[3]])
repro <- read_json(source_paths[[4]])
adapter <- read_json(source_paths[[5]])
trials <- read_json(source_paths[[6]])
check(nrow(mapping) == 78L && length(unique(mapping$mapping_id)) == 78L, "Expected 78 unique mappings")
check(all(mapping$outcome == "converted" & mapping$repeat_outcome == "converted"), "A mapping did not convert twice")
check(all(mapping$normalized_sample_sha256 == mapping$repeat_normalized_sample_sha256), "Within-mapping repeats differ")
check(corpus$mapping_denominator == nrow(mapping) && corpus$outcomes$converted == nrow(mapping), "Corpus denominator mismatch")
check(nrow(studies) == 8L && sum(studies$emg == "yes") == 7L, "Study scope changed")

# Panel A: formats are derived from the selected input, not from reader names.
order_ids <- c("study31", "study_28", "study_32", "study_33", "study_38", "study_43", "study_120", "study_1")
short_names <- c("Controlled-speed walking (31)", "Lifespan + stroke gait (28)",
                 "High-density EMG + IMU (32)", "Multi-condition locomotion (33)",
                 "Walking + stairs (38)", "Sit-to-walk transitions (43)",
                 "Gait120 (120)", "Ramp walking (1)*")
formats <- c("c3d", "mat", "txt", "csv", "trc", "mot")
mapping$format <- tolower(tools::file_ext(mapping$logical_source_member))
check(all(mapping$format %in% formats), "Unregistered source extension")
a <- expand.grid(study_id = order_ids, format = formats, stringsAsFactors = FALSE)
a$n <- mapply(function(s, f) sum(mapping$study_id == s & mapping$format == f), a$study_id, a$format)
a$tested <- a$n > 0
a$x <- 69 + (match(a$format, formats) - 1) * 14
a$y <- 54 - (match(a$study_id, order_ids) - 1) * 5.6
a$meaning <- ifelse(a$tested, "converted in both repetitions", "not tested in the frozen corpus")
counts <- data.frame(study_id = order_ids, label = short_names, y = 54 - (0:7) * 5.6)
counts$registered <- vapply(order_ids, function(s) sum(mapping$study_id == s), integer(1))
counts$converted <- counts$registered
check(all(counts$registered == studies$enabled_mappings[match(counts$study_id, studies$study_id)]), "Study mapping totals disagree")
write_csv(a[, c("study_id", "format", "n", "tested", "meaning")], "A_format_coverage")
write_csv(counts[, c("study_id", "label", "registered", "converted")], "A_study_totals")

# Panel B: recompute source/value concordance from each run's mapping ledger.
check(repro$run_count == 6L && repro$environment_count == 2L && isTRUE(repro$pass), "Reproducibility design changed")
check(repro$max_observed_numeric_difference == 0 && isTRUE(repro$exact_numeric_match), "Numeric identity no longer holds")
run_paths <- vapply(repro$runs, function(x) paste0("paper/results/validation/reproducibility_runs/", x$manifest), character(1))
source_paths <- c(source_paths, run_paths)
runs <- lapply(run_paths, read_json)
ref <- runs[[1]]
ledger <- function(run, field) {
  vals <- vapply(run$mapping_digests, function(x) x[[field]], character(1))
  ids <- vapply(run$mapping_digests, function(x) x$mapping_id, character(1))
  check(!anyDuplicated(ids), "Duplicate mapping ID in run")
  setNames(vals, ids)[sort(ids)]
}
b <- do.call(rbind, lapply(seq_along(runs), function(i) {
  run <- runs[[i]]
  check(!run$code_dirty && run$code_commit == repro$code_commit && run$dependency_lock_pass, "Run was not clean and locked")
  check(identical(run$locked_dependencies, ref$locked_dependencies), "Locked analysis dependencies differ")
  check(length(run$mapping_digests) == 78L, "Run mapping ledger incomplete")
  check(identical(ledger(run, "normalized_sample_sha256"), ledger(run, "repeat_normalized_sample_sha256")), "Within-run values differ")
  check(repro$runs[[i]]$scientific_digest == repro$reference_scientific_digest, "Run scientific content differs")
  data.frame(run_id = repro$runs[[i]]$run_id, environment = run$environment_id,
    converted = run$corpus_summary$outcomes$converted,
    registered = run$corpus_summary$mapping_denominator,
    source_identity_match = identical(ledger(run, "source_sha256"), ledger(ref, "source_sha256")),
    record_values_match = identical(ledger(run, "normalized_sample_sha256"), ledger(ref, "normalized_sample_sha256")))
}))
check(all(b$source_identity_match & b$record_values_match & b$converted == 78 & b$registered == 78), "Run concordance failure")
check(all(table(b$environment) == 3L), "Expected three runs per environment")
write_csv(b, "B_run_concordance")
b$y <- c(49, 43, 37, 27, 21, 15)
b$run <- rep(1:3, 2)

# Panel C: missingness is independently summed on source and normalized sides.
check(adapter$pass && adapter$core_changed_lines == 0 && adapter$core_dataset_id_branches == 0, "Held-out core-isolation endpoint failed")
check(adapter$adapter_changed_lines > 0, "Do not claim extension without an added reader")
check(adapter$repeat_count == 2 && length(unique(unlist(adapter$repeat_manifest_sha256))) == 1L, "Held-out repeats differ")
check(trials$normalized_manifest_sha256 == adapter$normalized_manifest_sha256, "Trial manifest does not match the executed adapter")
check(length(trials$trials) == adapter$trial_count && adapter$trial_count == 208L, "Held-out trial denominator mismatch")
subject_ids <- vapply(trials$trials, function(x) x$subject_id, character(1))
check(length(unique(subject_ids)) == adapter$subject_count && all(table(subject_ids) == 16L), "Held-out participant accounting mismatch")
trial_keys <- vapply(trials$trials, function(x) x$source_path, character(1))
check(!anyDuplicated(trial_keys), "Duplicate held-out trial")
missing <- do.call(rbind, lapply(trials$trials, function(t) {
  check(identical(names(t$source_missing_value_counts), names(t$nonfinite_value_counts)), "Missingness modality sets differ")
  check(identical(unlist(t$source_missing_value_counts), unlist(t$nonfinite_value_counts)), "A trial changed missingness")
  data.frame(subject = t$subject_id, trial = t$source_path,
             modality = names(t$source_missing_value_counts),
             source_missing = as.numeric(unlist(t$source_missing_value_counts)),
             retained_missing = as.numeric(unlist(t$nonfinite_value_counts)))
}))
check(sum(missing$source_missing) == 11528, "Missing-cell total changed")
c_data <- data.frame(measure = c("Participants", "Trials", "Missing cells"),
  input = c(adapter$subject_count, adapter$trial_count, sum(missing$source_missing)),
  retained = c(length(unique(subject_ids)), length(trial_keys), sum(missing$retained_missing)))
write_csv(c_data, "C_retention")
write_csv(aggregate(cbind(source_missing, retained_missing) ~ modality, missing, sum), "C_missingness_by_modality")

# White background, black type, one blue accent; all positions are in mm.
blue <- "#2B617E"
rule <- "#959DA3"
pt <- 72.27 / 25.4
text_layer <- function(x, y, label, size = 8.5, face = "plain", colour = "black", hjust = 0, vjust = 0.5) {
  annotate("text", x = x, y = y, label = label, size = size / pt,
           family = "Arial", fontface = face, colour = colour,
           hjust = hjust, vjust = vjust, lineheight = 1.05)
}
line_layer <- function(x, y, xend, yend = y, colour = rule, linewidth = 0.22, arrow = NULL) {
  parameters <- list(geom = "segment", x = x, y = y, xend = xend, yend = yend,
                     colour = colour, linewidth = linewidth)
  if (!is.null(arrow)) parameters$arrow <- arrow
  do.call(annotate, parameters)
}
canvas <- function(w, h) ggplot() + coord_cartesian(xlim = c(0, w), ylim = c(0, h), expand = FALSE, clip = "off") +
  theme_void(base_family = "Arial", base_size = 8.5) +
  theme(plot.margin = margin(0, 0, 0, 0), plot.background = element_rect(fill = "white", colour = NA))
panel_title <- function(letter, title, y) list(text_layer(2, y, letter, 13, "bold"), text_layer(10, y, title, 10.5, "bold"))

pA <- canvas(190, 80) + panel_title("A", "Heterogeneous public inputs were converted", 75) +
  text_layer(10, 64, "Public study", 8.5, "bold") +
  text_layer(104, 67, "Tested source formats", 8.5, "bold", hjust = 0.5) +
  text_layer(69 + (0:5) * 14, 61.5, toupper(formats), 8, hjust = 0.5) +
  text_layer(169, 64, "Conversion settings\ncompleted / registered", 7.5, "bold", hjust = 0.5) +
  line_layer(10, 58.5, 183) +
  geom_segment(data = a[!a$tested, ], aes(x = x - 0.8, xend = x + 0.8, y = y, yend = y), colour = rule, linewidth = 0.25) +
  geom_point(data = a[a$tested, ], aes(x = x, y = y), shape = 16, colour = blue, size = 2.1) +
  text_layer(10, counts$y, counts$label, 8.5) +
  text_layer(169, counts$y, paste0(counts$converted, "/", counts$registered), 8.5, hjust = 0.5) +
  line_layer(10, 18.4, 183, colour = "#E0E3E5") +
  line_layer(10, 10.5, 183) +
  annotate("point", x = 11, y = 5, shape = 16, colour = blue, size = 2.1) +
  text_layer(14, 5, "Converted twice", 8) +
  line_layer(47, 5, 49) + text_layer(51, 5, "Not tested", 8) +
  text_layer(83, 5, "*Biomechanics-only format control", 7.5) +
  text_layer(169, 5, "78/78", 9, "bold", blue, hjust = 0.5)

match_cells <- rbind(data.frame(x = 61, y = b$y, matched = b$source_identity_match),
                     data.frame(x = 81, y = b$y, matched = b$record_values_match))
pB <- canvas(93, 76) + panel_title("B", "Reproducible across environments", 70) +
  text_layer(9, 58, "System", 8, "bold") + text_layer(30, 58, "Run", 8, "bold", hjust = 0.5) +
  text_layer(44, 58, "Converted", 8, "bold", hjust = 0.5) +
  text_layer(61, 58, "Source\nidentity", 8, "bold", hjust = 0.5) +
  text_layer(81, 58, "Record\nvalues", 8, "bold", hjust = 0.5) +
  line_layer(9, 53.5, 89) +
  text_layer(9, 43, "macOS\narm64", 8) + text_layer(9, 21, "Ubuntu\nx86_64", 8) +
  text_layer(30, b$y, b$run, 8.5, hjust = 0.5) +
  text_layer(44, b$y, paste0(b$converted, "/", b$registered), 8.5, hjust = 0.5) +
  geom_point(data = match_cells[match_cells$matched, ], aes(x = x, y = y), shape = 16, size = 2.1, colour = blue) +
  line_layer(9, 32, 89, colour = "#E0E3E5") + line_layer(9, 10.5, 89) +
  annotate("point", x = 10, y = 6, shape = 16, colour = blue, size = 2.1) +
  text_layer(13, 6, "Exact match to reference", 8) +
  text_layer(9, 2.5, "Same code and locked analysis dependencies", 7.5)

arrow_spec <- arrow(type = "closed", length = unit(1.2, "mm"))
pC <- canvas(97, 76) + panel_title("C", "Extension without core changes", 70) +
  text_layer(10, 62, "ULTRA-MoCap: processed HDF5 release", 8) +
  text_layer(18, 51, "HDF5\ninput", 8.5, hjust = 0.5) +
  text_layer(45, 51, "New\nreader", 8.5, hjust = 0.5) +
  text_layer(79, 51, "Shared\nconverter", 8.5, hjust = 0.5) +
  line_layer(27, 51, 32, colour = "black", arrow = arrow_spec) +
  line_layer(57, 51, 65, colour = "black", arrow = arrow_spec) +
  text_layer(45, 43, "added", 7.5, colour = blue, hjust = 0.5) +
  text_layer(79, 43, "unchanged", 7.5, colour = blue, hjust = 0.5) +
  text_layer(56, 35, "Input", 8, "bold", hjust = 0.5) +
  text_layer(82, 35, "Retained", 8, "bold", hjust = 0.5) +
  line_layer(10, 31.5, 92) +
  text_layer(10, c(27, 20.5, 14), c_data$measure, 8.5) +
  text_layer(56, c(27, 20.5, 14), format(c_data$input, big.mark = ",", trim = TRUE), 8.5, hjust = 0.5) +
  text_layer(82, c(27, 20.5, 14), format(c_data$retained, big.mark = ",", trim = TRUE), 8.5, hjust = 0.5) +
  line_layer(10, 10.5, 92) +
  text_layer(10, 6, "Two conversions; missing values not imputed", 7.5) +
  text_layer(10, 2.5, "No dataset-specific branch in the shared converter", 7.5)

draw <- function() {
  grid.newpage()
  pushViewport(viewport(layout = grid.layout(2, 2, heights = unit(c(80, 76), "mm"), widths = unit(c(93, 97), "mm"))))
  print(pA, newpage = FALSE, vp = viewport(layout.pos.row = 1, layout.pos.col = 1:2))
  print(pB, newpage = FALSE, vp = viewport(layout.pos.row = 2, layout.pos.col = 1))
  print(pC, newpage = FALSE, vp = viewport(layout.pos.row = 2, layout.pos.col = 2))
  popViewport()
}
pdf_path <- file.path(out, paste0(stem, ".pdf"))
if (Sys.info()[["sysname"]] == "Darwin") {
  grDevices::quartz(type = "pdf", file = pdf_path, width = 190 / 25.4,
                   height = 156 / 25.4, family = "Arial", title = "Figure 2. Reproducible conversion and held-out extension")
} else {
  grDevices::cairo_pdf(pdf_path, width = 190 / 25.4, height = 156 / 25.4, family = "Arial", onefile = TRUE)
}
draw()
invisible(dev.off())
svglite::svglite(file.path(out, paste0(stem, ".svg")), width = 190 / 25.4, height = 156 / 25.4, bg = "white")
draw()
invisible(dev.off())
ragg::agg_png(file.path(out, paste0(stem, ".png")), width = 190, height = 156, units = "mm", res = 600, background = "white")
draw()
invisible(dev.off())
writeLines(trimws(capture.output(sessionInfo()), which = "right"), file.path(out, paste0(stem, "_session.txt")))
source_paths <- c(source_paths, "scripts/paper/generate_figure2_ggplot.R", "paper/figures/candidates/FIGURE2_VERSION",
                  paste0("paper/figures/candidates/", stem, "/", stem, "_caption.md"))
manifest <- list(figure = 2, status = "candidate_not_adopted", version = version,
  graphic_engine = paste("ggplot2", packageVersion("ggplot2")),
  width_mm = 190, height_mm = 156, raster_dpi = 600, minimum_text_pt = 7.5,
  title_text_pt = 10.5, normal_text_pt = 8.5, font = "Arial",
  visual_encoding = "One blue accent; black text; white background; categorical formats and exact result tables",
  checks = list(mapping_count = nrow(mapping), study_count = nrow(studies),
    successful_repeats = 2, corpus_run_count = nrow(b), all_source_identities_match = all(b$source_identity_match),
    all_record_values_match = all(b$record_values_match), core_changed_lines = adapter$core_changed_lines,
    dataset_specific_core_branches = adapter$core_dataset_id_branches,
    added_adapter_lines = adapter$adapter_changed_lines,
    held_out_trials = length(trial_keys), missing_cells = sum(missing$source_missing),
    source_and_retained_missingness_match_for_every_trial = TRUE),
  scope = "Bounded conversion and reproducibility; no amplitude pooling or universal EMG comparability claim",
  sources = lapply(source_paths, function(p) list(path = p, sha256 = digest::digest(file = file.path(root, p), algo = "sha256"))))
write_json(manifest, file.path(out, paste0(stem, "_evidence.json")), pretty = TRUE, auto_unbox = TRUE)
cat("Created Figure 2 candidate:", out, "\n")
