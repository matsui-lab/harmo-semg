# ============================================================================
# Quick Start Guide: Analysis & Visualization
# ============================================================================
#
# This script provides a quick start guide for running the analysis pipeline
# Source this file or run the examples step-by-step

# Prerequisites:
#   - R packages: duckdb, DBI, ggplot2, reshape2, NMF, dplyr, tidyr, PhysioExperiment, SummarizedExperiment, S4Vectors
#   - iceberg_to_physio.R must be sourced
#   - analysis_visualization.R must be sourced

# Install required packages if not already installed
required_packages <- c("duckdb", "DBI", "ggplot2", "reshape2", "NMF", "dplyr", "tidyr", "PhysioExperiment", "SummarizedExperiment", "S4Vectors")
for (pkg in required_packages) {
  if (!require(pkg, character.only = TRUE)) {
    message("Installing ", pkg, "...")
    install.packages(pkg)
  }
}

library(duckdb)
library(DBI)
library(ggplot2)
library(reshape2)
library(NMF)
library(dplyr)
library(tidyr)
library(PhysioExperiment)
library(SummarizedExperiment)
library(S4Vectors)

# Source the function definitions
# Resolve paths relative to this script's location
.script_dir <- if (exists(".harmo_scripts")) {
  .harmo_scripts
} else {
  tryCatch(
    dirname(sys.frame(1)$ofile),
    error = function(e) getwd()
  )
}
source(file.path(.script_dir, "iceberg_to_physio.R"))
source(file.path(.script_dir, "analysis_visualization.R"))

# ============================================================================
# Parameter resolution: CLI (HARMO_R_PARAMS) or defaults
# ============================================================================

# Read parameters from environment (set by `harmo r-analyze`)
.r_params_json <- Sys.getenv("HARMO_R_PARAMS", unset = "")
if (nzchar(.r_params_json)) {
  if (!requireNamespace("jsonlite", quietly = TRUE)) {
    stop("jsonlite package required for CLI parameter parsing. ",
         "Install with: install.packages('jsonlite')", call. = FALSE)
  }
  .cli_params <- jsonlite::fromJSON(.r_params_json)
  message("[harmo] Using parameters from CLI (HARMO_R_PARAMS)")
} else {
  .cli_params <- list()
}

# Parameter defaults — override these for interactive use
.study_id          <- .cli_params$study_id          %||% 31
.subject_id        <- .cli_params$subject_id        %||% 1
.session_id        <- .cli_params$session_id        %||% "ses-V3-T5"
.run_id            <- .cli_params$run_id            %||% "20260209_0223_default"
.n_synergies       <- .cli_params$n_synergies       %||% 4
.emg_output_dir    <- .cli_params$emg_output_dir    %||% "emg_analysis"
.synergy_output_dir <- .cli_params$synergy_output_dir %||% "synergy_analysis"

# Warehouse path: HARMO_WAREHOUSE_PATH env or default
.warehouse_path <- Sys.getenv("HARMO_WAREHOUSE_PATH", unset = "")
if (!nzchar(.warehouse_path)) {
  .warehouse_path <- NULL  # let connectToIceberg() use its default
}

# ============================================================================
# QUICKSTART: Run complete analysis
# ============================================================================

# Step 1: Connect to Iceberg
message("Connecting to Iceberg warehouse...")
con <- connectToIceberg(warehouse_path = .warehouse_path)

# Step 2: Run the analysis pipeline
results <- run_analysis_pipeline(
  con = con,
  study_id = .study_id,
  subject_id = .subject_id,
  session_id = .session_id,
  run_id = .run_id,
  n_synergies = .n_synergies,
  emg_output_dir = .emg_output_dir,
  synergy_output_dir = .synergy_output_dir
)

# Step 3: Disconnect
disconnectFromIceberg(con)

message("\n✓ Analysis complete! Check the output directories for plots:")
message("  - EMG plots: ", .emg_output_dir)
message("  - Synergy plots: ", .synergy_output_dir)

# ============================================================================
# ADVANCED: Run analyses separately
# ============================================================================

# If you need to run analyses individually:

# --- A. Only EMG Visualization ---
# con <- connectToIceberg()
#
# pe_emg <- fetchPhysioExperiment(
#   con = con,
#   study_id = 31,
#   subject_id = 1,
#   session_id = "ses-V3-T5",
#   table_name = "raw_data",
#   sensor_type = "Surface-EMG"
# )
#
# emg_plots <- visualize_surface_emg(pe_emg, output_dir = "emg_analysis")
# disconnectFromIceberg(con)

# --- B. Only Synergy Analysis ---
# con <- connectToIceberg()
#
# # Fetch activation data
# activation_df <- .fetch_activation_data(
#   con = con,
#   study_id = 31,
#   subject_id = 1,
#   session_id = "ses-V3-T5",
#   run_id = "20260209_0223_default"
# )
#
# # Convert to matrix
# activation_matrix <- activation_df %>%
#   pivot_wider(
#     id_cols = time,
#     names_from = muscle,
#     values_from = value,
#     values_fill = 0
#   ) %>%
#   as.data.frame()
#
# time_vector <- activation_matrix$time
# activation_matrix <- as.matrix(activation_matrix[, -1])
# activation_matrix <- activation_matrix / apply(activation_matrix, 2, max, na.rm = TRUE)
# activation_matrix[is.na(activation_matrix)] <- 0
#
# # Run NMF
# synergy_result <- perform_nmf_synergy_analysis(
#   activation_matrix = activation_matrix,
#   n_synergies = 4
# )
#
# # Visualize
# visualize_synergy_analysis(synergy_result, time_vector, output_dir = "synergy_analysis")
# disconnectFromIceberg(con)

# ============================================================================
# TIPS
# ============================================================================
#
# 1. Finding available data:
#    con <- connectToIceberg()
#    studies <- listAvailableStudies(con)
#    sessions <- listAvailableSessions(con, study_id = 31, subject_id = 1)
#    disconnectFromIceberg(con)
#
# 2. Number of synergies:
#    - Start with 4-5 synergies
#    - Increase if global patterns are missed
#    - Decrease if synergies become too specific
#
# 3. Output plots:
#    01_emg_timeseries_stacked.png - Raw EMG signals (stacked view using plotMultiChannel)
#    02_emg_butterfly.png - Raw EMG signals (butterfly/overlaid view using plotMultiChannel)
#    03_emg_statistics.png - Distribution and statistics (boxplot)
#    01_synergy_vectors.png - Muscle weights for each synergy (using PhysioExperiment)
#    02_synergy_time_coefficients.png - Activation patterns over time (using PhysioExperiment)
#    03_actual_vs_reconstructed.png - Model reconstruction quality comparison
#    04_synergy_heatmap.png - Overall muscle-synergy relationships heatmap
#
# 4. Interpretation:
#    - Muscle synergies represent coordinated groups of muscles
#    - Similar weights in synergy vectors suggest co-activation
#    - Time coefficients show when synergies are active
#    - Reconstruction accuracy indicates model quality
