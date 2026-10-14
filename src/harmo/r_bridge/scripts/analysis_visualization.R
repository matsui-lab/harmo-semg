# ============================================================================
# Analysis and Visualization Script
# ============================================================================
#
# This script performs two main analyses:
# 1. Visualize raw_data Surface-EMG signals
# 2. Perform synergy analysis on osim_value data (activation columns)
#
# Prerequisites:
#   - Source iceberg_to_physio.R
#   - Connected to Iceberg warehouse (connectToIceberg() called)
#   - PhysioExperiment package installed

library(duckdb)
library(DBI)
library(ggplot2)
library(reshape2)
library(NMF)  # For synergy analysis
library(dplyr)
library(tidyr)  # For pivot_wider
library(PhysioExperiment)  # For visualization and signal processing
library(SummarizedExperiment)  # For assay access
library(S4Vectors)  # For DataFrame creation

# ============================================================================
# PART 1: Visualize Surface-EMG Data
# ============================================================================

#' Visualize Raw Surface-EMG Data
#'
#' Generates EMG signal visualizations using PhysioExperiment package functions.
#' Includes multi-channel plots, heatmaps, and summary statistics.
#'
#' @param pe PhysioExperiment object loaded from fetchPhysioExperiment()
#' @param output_dir Directory to save plots
#' @export
visualize_surface_emg <- function(pe, output_dir = "~/emg_plots") {
  
  # Create output directory if needed
  dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)
  
  # Validate PhysioExperiment object
  stopifnot(inherits(pe, "PhysioExperiment"))
  
  # Extract metadata
  metadata <- metadata(pe)
  study_info <- sprintf("Study %d | Subject %d | Session %s",
                        metadata$study_id,
                        metadata$subject_id,
                        metadata$session_id)
  
  message("Generating EMG visualization plots...")
  message(study_info)
  
  # ---- Plot 1: Multi-channel time series using plotMultiChannel()
  message("  - Creating multi-channel time series plot (stacked view)...")
  
  # Use PhysioExperiment's plotMultiChannel with stacked style for EMG
  p1 <- plotMultiChannel(pe, style = "stacked", assay_name = "measured")
  p1 <- p1 + 
    ggplot2::labs(
      title = paste("Surface-EMG Time Series -", study_info),
      caption = paste("Sampling Rate:", round(samplingRate(pe), 2), "Hz")
    )
  
  ggsave(file.path(output_dir, "01_emg_timeseries_stacked.png"),
         p1, width = 14, height = 10, dpi = 300)
  
  # ---- Plot 2: Multi-channel butterfly plot (overlaid)
  message("  - Creating multi-channel butterfly plot...")
  
  p2 <- plotMultiChannel(pe, style = "butterfly", assay_name = "measured")
  p2 <- p2 +
    ggplot2::labs(
      title = paste("Surface-EMG Butterfly Plot (Overlaid) -", study_info),
      caption = paste("Sampling Rate:", round(samplingRate(pe), 2), "Hz")
    )
  
  ggsave(file.path(output_dir, "02_emg_butterfly.png"),
         p2, width = 12, height = 8, dpi = 300)
  
  # ---- Plot 3: Signal amplitude distribution (boxplot)
  message("  - Creating amplitude distribution plot...")
  
  # Extract signal data and channel names
  signal_matrix <- SummarizedExperiment::assay(pe, "measured")
  channel_names <- colnames(signal_matrix)
  
  # Convert to long format for boxplot
  df_stats <- data.frame(
    channel = rep(channel_names, each = nrow(signal_matrix)),
    amplitude = as.vector(signal_matrix),
    stringsAsFactors = FALSE
  )
  
  p3 <- ggplot2::ggplot(df_stats, ggplot2::aes(x = channel, y = amplitude, fill = channel)) +
    ggplot2::geom_boxplot(alpha = 0.7) +
    ggplot2::theme_minimal() +
    ggplot2::theme(
      axis.text.x = ggplot2::element_text(angle = 90, vjust = 0.5, hjust = 1),
      legend.position = "none"
    ) +
    ggplot2::labs(
      title = paste("Surface-EMG Signal Statistics -", study_info),
      x = "Channel",
      y = "Amplitude (mV)"
    )
  
  ggsave(file.path(output_dir, "03_emg_statistics.png"),
         p3, width = 12, height = 6, dpi = 300)
  
  # ---- Print summary statistics
  message("\nSummary Statistics:")
  summary_stats <- data.frame(
    Channel = channel_names,
    Mean = colMeans(signal_matrix, na.rm = TRUE),
    SD = apply(signal_matrix, 2, stats::sd, na.rm = TRUE),
    Min = apply(signal_matrix, 2, min, na.rm = TRUE),
    Max = apply(signal_matrix, 2, max, na.rm = TRUE),
    RMS = sqrt(colMeans(signal_matrix^2, na.rm = TRUE))
  )
  print(summary_stats)
  
  message("\nPlots saved to: ", output_dir)
  invisible(list(
    plot_stacked = p1,
    plot_butterfly = p2,
    plot_stats = p3,
    summary_stats = summary_stats
  ))
}

# ============================================================================
# PART 2: Synergy Analysis from OpenSim Data
# ============================================================================

#' Fetch and Prepare Activation Data for Synergy Analysis
#'
#' Retrieves activation columns from osim_value that match:
#' - Method: MocoInverse
#' - Output contains "coord_" in the name
#' - Column contains "activation"
#'
#' @param con DBI connection to Iceberg
#' @param study_id Study ID
#' @param subject_id Subject ID
#' @param session_id Session ID
#' @param run_id Run ID for MocoInverse analysis
#' @return Data frame with time, muscle activation columns
#' @keywords internal
.fetch_activation_data <- function(con, study_id, subject_id, session_id, run_id) {
  warehouse <- attr(con, "iceberg_warehouse")
  
  # Query for activation data from MocoInverse
  query <- sprintf("
    SELECT
      d.time,
      d.variable as muscle,
      d.value,
      o.output_name,
      o.unit
    FROM iceberg_scan('%s/osim_value', allow_moved_paths = true) AS d
    LEFT JOIN iceberg_scan('%s/osim_output', allow_moved_paths = true) AS o
      ON d.output_id = o.output_id AND d.method_id = o.method_id
    WHERE d.run_id = '%s'
      AND d.study_id = %d
      AND d.subject_id = '%s'
      AND d.session_id = '%s'
      AND o.output_name LIKE 'coord_%%'
      AND d.variable LIKE '%%activation%%'
    ORDER BY d.time, d.variable
  ", warehouse, warehouse, run_id, study_id, subject_id, session_id)
  
  result <- DBI::dbGetQuery(con, query)
  return(result)
}

#' Perform Non-negative Matrix Factorization (NMF) for Muscle Synergies
#'
#' Decomposes muscle activation patterns into synergies using NMF.
#'
#' @param activation_matrix Matrix of muscle activations (time × muscles)
#' @param n_synergies Number of synergies to extract (default: 4)
#' @param method NMF method ("lee", "snmf", etc.)
#' @return List containing:
#'   - model: NMF model object
#'   - W: Synergy weights (muscles × synergies)
#'   - H: Synergy time coefficients (synergies × time)
#'   - var_explained: Variance explained by each synergy
#'
#' @export
perform_nmf_synergy_analysis <- function(activation_matrix,
                                         n_synergies = 4,
                                         method = "lee") {
  
  message("Performing NMF for muscle synergy analysis...")
  message("  - Activation matrix: ", nrow(activation_matrix), " time points × ",
          ncol(activation_matrix), " muscles")
  message("  - Number of synergies: ", n_synergies)
  
  # Ensure non-negative values
  activation_matrix[activation_matrix < 0] <- 0
  
  # Run NMF
  nmf_model <- NMF::nmf(activation_matrix,
                        rank = n_synergies,
                        method = method,
                        nrun = 10,
                        seed = 123)
  
  # Extract components
  # Note: For activation_matrix [time × muscles], NMF returns:
  # W (basis): [time × synergies]
  # H (coef): [synergies × muscles]
  W <- basis(nmf_model)      # Time coefficients (time × synergies)
  H <- coef(nmf_model)       # Muscle weights (synergies × muscles)
  
  # Calculate variance explained
  total_var <- sum(activation_matrix^2)
  reconstructed <- W %*% H
  residual_var <- sum((activation_matrix - reconstructed)^2)
  var_explained <- (1 - residual_var / total_var) * 100
  
  message("  - Variance explained: ", round(var_explained, 2), "%")
  
  return(list(
    model = nmf_model,
    W = W,
    H = H,
    total_variance_explained = var_explained,
    activation_matrix = activation_matrix
  ))
}

#' Visualize Synergy Analysis Results
#'
#' Creates visualization plots for NMF synergy analysis using PhysioExperiment
#' integration for consistent visualization approach.
#'
#' @param synergy_result Result from perform_nmf_synergy_analysis()
#' @param time_vector Time vector corresponding to activations
#' @param output_dir Directory to save plots
#' @export
visualize_synergy_analysis <- function(synergy_result, time_vector, output_dir = "~/synergy_plots") {
  
  dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)
  
  W <- synergy_result$W              # [time × synergies]
  H <- synergy_result$H              # [synergies × muscles]
  activation_matrix <- synergy_result$activation_matrix  # [time × muscles]
  n_synergies <- ncol(W)
  n_muscles <- ncol(H)
  n_time_points <- nrow(W)
  
  # Get muscle and synergy names
  muscle_names <- colnames(activation_matrix)
  if (is.null(muscle_names)) {
    muscle_names <- paste0("Muscle_", seq_len(n_muscles))
    colnames(activation_matrix) <- muscle_names
  }
  synergy_names <- paste0("Synergy_", seq_len(n_synergies))
  
  message("Generating synergy visualization plots...")
  
  # ---- Plot 1: Synergy vectors (muscle weights) as bar chart
  message("  - Creating synergy vectors plot...")
  
  # Create PhysioExperiment object for synergy weights
  # H: [synergies × muscles] -> transpose to [muscles × synergies]
  H_transposed <- t(H)
  
  # Build data frame for barplot - each synergy separately
  # Get muscle names from colnames
  muscle_labels <- colnames(H_transposed)
  if (is.null(muscle_labels)) {
    muscle_labels <- seq_len(nrow(H_transposed))
  }
  
  # Create barplot data
  df_synergy_vectors <- data.frame()
  for (syn_idx in seq_len(n_synergies)) {
    df_temp <- data.frame(
      muscle_idx = seq_len(n_muscles),
      muscle_label = muscle_labels,
      weight = H_transposed[, syn_idx],
      synergy = synergy_names[syn_idx],
      stringsAsFactors = FALSE
    )
    df_synergy_vectors <- rbind(df_synergy_vectors, df_temp)
  }
  
  # Create barplot with facets
  p1 <- ggplot2::ggplot(df_synergy_vectors, 
                        ggplot2::aes(x = factor(muscle_label, levels = unique(muscle_labels)), 
                                     y = weight, fill = synergy)) +
    ggplot2::geom_col(alpha = 0.8) +
    ggplot2::facet_wrap(~synergy, ncol = 2) +
    ggplot2::theme_minimal() +
    ggplot2::theme(
      axis.text.x = ggplot2::element_text(size = 6, angle = 90, vjust = 0.5, hjust = 1),
      legend.position = "none"
    ) +
    ggplot2::labs(
      title = "Muscle Synergy Vectors (NMF Decomposition)",
      x = "Muscle",
      y = "Normalized Weight",
      fill = "Synergy"
    )
  
  ggsave(file.path(output_dir, "01_synergy_vectors.png"),
         p1, width = 14, height = 10, dpi = 300)
  
  # ---- Plot 2: Synergy time coefficients
  message("  - Creating synergy time coefficients plot...")
  
  # Build data frame directly from W and time_vector
  # W: [time × synergies]
  # Validate time_vector length
  if (length(time_vector) != n_time_points) {
    message("Warning: time_vector length (", length(time_vector), 
            ") does not match W rows (", n_time_points, ")")
    time_to_use <- seq_len(n_time_points)
  } else {
    time_to_use <- time_vector
  }
  
  # Create data frame for synergy time coefficients
  df_synergy_coeff <- data.frame(
    time = rep(time_to_use, n_synergies),
    coefficient = as.vector(W),
    synergy = rep(synergy_names, each = n_time_points),
    stringsAsFactors = FALSE
  )
  
  # Plot synergy coefficients as lines with distinct colors
  p2 <- ggplot2::ggplot(df_synergy_coeff, 
                        ggplot2::aes(x = time, y = coefficient, 
                                     color = synergy, linetype = synergy)) +
    ggplot2::geom_line(size = 0.8) +
    ggplot2::facet_wrap(~synergy, ncol = 2) +
    ggplot2::theme_minimal() +
    ggplot2::theme(legend.position = "top") +
    ggplot2::labs(
      title = "Synergy Time Coefficients (Activation Patterns)",
      x = "Time (s)",
      y = "Normalized Coefficient",
      color = "Synergy",
      linetype = "Synergy"
    )
  
  ggsave(file.path(output_dir, "02_synergy_time_coefficients.png"),
         p2, width = 14, height = 10, dpi = 300)
  
  # ---- Plot 3: Actual vs Reconstructed activations
  message("  - Creating actual vs reconstructed plot...")
  
  # Reconstruct activations: W [time × synergies] × H [synergies × muscles]
  reconstructed <- W %*% H  # Should be [time × muscles]
  
  # Validate dimensions
  if (nrow(activation_matrix) != nrow(reconstructed) || ncol(activation_matrix) != ncol(reconstructed)) {
    stop("Reconstruction dimensions do not match activation_matrix")
  }
  
  # Create PhysioExperiment objects for actual and reconstructed data
  pe_actual <- PhysioExperiment::PhysioExperiment(
    assays = list(activations = activation_matrix),
    colData = S4Vectors::DataFrame(label = muscle_names, type = "muscle"),
    samplingRate = if (length(time_vector) > 1) {
      1 / (time_vector[2] - time_vector[1])
    } else {
      1
    }
  )
  
  pe_reconstructed <- PhysioExperiment::PhysioExperiment(
    assays = list(activations = reconstructed),
    colData = S4Vectors::DataFrame(label = muscle_names, type = "muscle"),
    samplingRate = samplingRate(pe_actual)
  )
  
  # Select subset of muscles for clarity (max 4)
  n_to_show <- min(4, n_muscles)
  sample_muscle_indices <- round(seq(1, n_muscles, length.out = n_to_show))
  
  # Build comparison data frame
  df_comparison <- data.frame()
  for (idx in sample_muscle_indices) {
    muscle_name <- muscle_names[idx]
    actual_vals <- activation_matrix[, idx]
    recon_vals <- reconstructed[, idx]
    
    # Validate time_vector
    if (length(time_vector) != n_time_points) {
      time_to_use <- seq_len(n_time_points)
    } else {
      time_to_use <- time_vector
    }
    
    df_temp <- data.frame(
      time = c(time_to_use, time_to_use),
      amplitude = c(actual_vals, recon_vals),
      type = c(rep("Actual", n_time_points), rep("Reconstructed", n_time_points)),
      muscle = muscle_name,
      stringsAsFactors = FALSE
    )
    df_comparison <- rbind(df_comparison, df_temp)
  }
  
  p3 <- ggplot2::ggplot(df_comparison, 
                        ggplot2::aes(x = time, y = amplitude, 
                                     color = type, linetype = type)) +
    ggplot2::geom_line() +
    ggplot2::facet_wrap(~muscle, ncol = 2, scales = "free") +
    ggplot2::theme_minimal() +
    ggplot2::theme(legend.position = "top") +
    ggplot2::labs(
      title = "Actual vs Reconstructed Muscle Activations",
      x = "Time (s)",
      y = "Normalized Activation",
      color = "Type",
      linetype = "Type"
    )
  
  ggsave(file.path(output_dir, "03_actual_vs_reconstructed.png"),
         p3, width = 14, height = 10, dpi = 300)
  
  # ---- Plot 4: Synergy heatmap
  message("  - Creating synergy heatmap...")
  
  # Muscle synergy relationships from H [synergies × muscles]
  # Manual data frame construction for heatmap
  H_transposed <- t(H)  # [muscles × synergies]
  
  df_heatmap <- data.frame(
    muscle = rep(muscle_names, each = n_synergies),
    synergy = rep(synergy_names, n_muscles),
    weight = as.vector(H_transposed),
    stringsAsFactors = FALSE
  )
  
  p4 <- ggplot2::ggplot(df_heatmap, 
                        ggplot2::aes(x = synergy, y = muscle, fill = weight)) +
    ggplot2::geom_raster() +
    ggplot2::scale_fill_gradient(low = "white", high = "red") +
    ggplot2::theme_minimal() +
    ggplot2::theme(
      axis.text.y = ggplot2::element_text(size = 8),
      axis.text.x = ggplot2::element_text(angle = 45, vjust = 1, hjust = 1)
    ) +
    ggplot2::labs(
      title = "Muscle Synergy Heatmap",
      x = "Synergy",
      y = "Muscle",
      fill = "Weight"
    )
  
  ggsave(file.path(output_dir, "04_synergy_heatmap.png"),
         p4, width = 10, height = 12, dpi = 300)
  
  # ---- Print summary statistics
  message("\nSynergy Analysis Summary:")
  message("  - Total Synergies: ", n_synergies)
  message("  - Total Muscles: ", n_muscles)
  message("  - Variance Explained: ", round(synergy_result$total_variance_explained, 2), "%")
  message("  - Reconstruction Error: ", round(100 - synergy_result$total_variance_explained, 2), "%")
  
  message("\nPlots saved to: ", output_dir)
  invisible(list(
    plot_vectors = p1,
    plot_coefficients = p2,
    plot_comparison = p3,
    plot_heatmap = p4,
    results = synergy_result
  ))
}

# ============================================================================
# Main Execution Function
# ============================================================================

#' Run Complete Analysis Pipeline
#'
#' Executes both EMG visualization and synergy analysis
#'
#' @param con DBI connection to Iceberg (from connectToIceberg())
#' @param study_id Study ID
#' @param subject_id Subject ID
#' @param session_id Raw data session ID
#' @param run_id OpenSim MocoInverse run ID
#' @param n_synergies Number of synergies for NMF
#' @param emg_output_dir Output directory for EMG plots
#' @param synergy_output_dir Output directory for synergy plots
#' @export
run_analysis_pipeline <- function(con,
                                  study_id,
                                  subject_id,
                                  session_id,
                                  run_id,
                                  n_synergies = 4,
                                  emg_output_dir = "~/emg_plots",
                                  synergy_output_dir = "~/synergy_plots") {
  
  message("\n", strrep("=", 70))
  message("STARTING ANALYSIS PIPELINE")
  message(strrep("=", 70))
  
  # ---- Part 1: EMG Visualization
  message("\n[PART 1] Visualizing Surface-EMG Data")
  message(strrep("-", 70))
  
  tryCatch({
    pe_emg <- fetchPhysioExperiment(
      con = con,
      study_id = study_id,
      subject_id = subject_id,
      session_id = session_id,
      table_name = "raw_data",
      sensor_type = "Surface-EMG"
    )
    
    emg_results <- visualize_surface_emg(pe_emg, output_dir = emg_output_dir)
    message("\n✓ EMG visualization completed successfully")
    
  }, error = function(e) {
    message("\n✗ Error in EMG visualization: ", e$message)
    return(NULL)
  })
  
  # ---- Part 2: Synergy Analysis
  message("\n[PART 2] Analyzing Muscle Synergies from OpenSim")
  message(strrep("-", 70))
  
  synergy_result <- NULL
  tryCatch({
    # Fetch activation data via fetchOsimPhysioExperiment
    message("Fetching activation data from OpenSim...")
    pe_osim <- fetchOsimPhysioExperiment(
      con = con,
      run_id = run_id,
      study_id = study_id,
      subject_id = subject_id,
      session_id = session_id,
      variable_filter = "%activation%"
    )
    
    # Extract activation matrix from PhysioExperiment
    activation_matrix <- SummarizedExperiment::assay(pe_osim, "osim_value")
    muscle_names <- colnames(activation_matrix)
    time_vector <- SummarizedExperiment::rowData(pe_osim)$time
    
    message("  - Found ", nrow(activation_matrix), " time points, ",
            ncol(activation_matrix), " muscles")
    message("  - Muscles: ", paste(muscle_names, collapse = ", "))
    
    # Normalize activations to [0, 1] for better NMF performance
    col_max <- apply(activation_matrix, 2, max, na.rm = TRUE)
    col_max[col_max == 0] <- 1  # avoid division by zero
    activation_matrix <- sweep(activation_matrix, 2, col_max, "/")
    activation_matrix[is.na(activation_matrix)] <- 0
    
    # Perform NMF
    synergy_result <- perform_nmf_synergy_analysis(
      activation_matrix = activation_matrix,
      n_synergies = n_synergies
    )
    
    # Visualize results
    visualize_synergy_analysis(synergy_result, time_vector, output_dir = synergy_output_dir)
    
    message("\n✓ Synergy analysis completed successfully")
    
  }, error = function(e) {
    message("\n✗ Error in synergy analysis: ", e$message)
    return(NULL)
  })
  
  message("\n", strrep("=", 70))
  message("ANALYSIS PIPELINE COMPLETE")
  message(strrep("=", 70))
  
  invisible(list(emg_results = emg_results, synergy_result = synergy_result))
}

# ============================================================================
# Usage Example
# ============================================================================

# # Source the iceberg connection functions first
# source("iceberg_to_physio.R")
# con <- connectToIceberg()
#
# # Run the analysis pipeline
# analysis_output <- run_analysis_pipeline(
#   con = con,
#   study_id = 31,
#   subject_id = 1,
#   session_id = "ses-V3-T5",
#   run_id = "20260209_0223_default",
#   n_synergies = 4,
#   emg_output_dir = "~/emg_analysis",
#   synergy_output_dir = "~/synergy_analysis"
# )
#
# # Disconnect when done
# disconnectFromIceberg(con)
