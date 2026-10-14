#' Fetch from Iceberg Tables and Create PhysioExperiment Objects
#'
#' This module provides functions to load data from Apache Iceberg tables
#' in DuckDB and construct PhysioExperiment objects.
#'
#' Warehouse location is assumed to be: warehouse/warehouse/
#' relative to the directory containing this script.

library(duckdb)
library(DBI)
library(SummarizedExperiment)
library(S4Vectors)
library(PhysioExperiment)

# ============================================================================
# Database Connection Functions
# ============================================================================

#' Connect to Iceberg Warehouse in DuckDB
#'
#' @param warehouse_path Path to the Iceberg warehouse directory.
#'        Defaults to "warehouse/warehouse/" relative to current working directory.
#' @return A DBI connection to DuckDB with Iceberg extension loaded.
#' @export
connectToIceberg <- function(warehouse_path = NULL) {
  if (is.null(warehouse_path)) {
    # Check environment variable first (set by harmo CLI or Posit Workbench setup)
    env_path <- Sys.getenv("HARMO_WAREHOUSE_PATH", unset = "")
    if (nzchar(env_path)) {
      warehouse_path <- env_path
    } else {
      warehouse_path <- file.path(getwd(), "warehouse", "warehouse")
    }
  }
  
  # Verify warehouse path exists
  if (!dir.exists(warehouse_path)) {
    stop("Warehouse path does not exist: ", warehouse_path, call. = FALSE)
  }
  
  # Connect to DuckDB and load Iceberg extension
  con <- DBI::dbConnect(duckdb::duckdb())
  tryCatch({
    DBI::dbExecute(con, "INSTALL iceberg; LOAD iceberg;")
  }, error = function(e) {
    DBI::dbDisconnect(con)
    stop("Failed to load Iceberg extension: ", e$message, call. = FALSE)
  })
  
  # Store warehouse path as connection attribute for reference
  attr(con, "iceberg_warehouse") <- warehouse_path
  return(con)
}

#' Disconnect from Iceberg Database
#'
#' @param con A DBI connection from connectToIceberg().
#' @export
disconnectFromIceberg <- function(con) {
  if (inherits(con, "DBIConnection")) {
    DBI::dbDisconnect(con)
  }
}

# ============================================================================
# Metadata Fetchers
# ============================================================================

#' Fetch Subject Metadata from subject_table
#'
#' @param con DBI connection to Iceberg.
#' @param study_id Study ID filter (optional).
#' @param subject_id Subject ID filter (optional).
#' @return Data frame with columns: study_id, subject_id, height_m, weight_kg,
#'         age_year, sex, pathlogy, bmi, notes
#' @keywords internal
.fetchSubjectMetadata <- function(con, study_id = NULL, subject_id = NULL) {
  warehouse <- attr(con, "iceberg_warehouse")
  
  query <- sprintf("
    SELECT *
    FROM iceberg_scan('%s/subject_table', allow_moved_paths = true)
    WHERE 1=1
  ", warehouse)
  
  if (!is.null(study_id)) {
    query <- paste0(query, sprintf(" AND study_id = %d", study_id))
  }
  if (!is.null(subject_id)) {
    query <- paste0(query, sprintf(" AND subject_id = %d", subject_id))
  }
  
  result <- DBI::dbGetQuery(con, query)
  return(result)
}

#' Fetch Device/Sampling Rate Information
#'
#' @param con DBI connection to Iceberg.
#' @param study_id Study ID.
#' @return Data frame with columns: device_id, device_name, type, samplingrate_Hz, etc.
#' @keywords internal
.fetchDeviceInfo <- function(con, study_id) {
  warehouse <- attr(con, "iceberg_warehouse")
  
  query <- sprintf("
    SELECT *
    FROM iceberg_scan('%s/device_table', allow_moved_paths = true)
    WHERE study_id = %d
  ", warehouse, study_id)
  
  result <- DBI::dbGetQuery(con, query)
  return(result)
}

#' Fetch Channel Mapping Information
#'
#' @param con DBI connection to Iceberg.
#' @param study_id Study ID.
#' @param sensor_type Sensor type filter (e.g., "Surface-EMG").
#' @return Data frame with channel mapping information.
#' @keywords internal
.fetchChannelMapping <- function(con, study_id, sensor_type = NULL) {
  warehouse <- attr(con, "iceberg_warehouse")
  
  query <- sprintf("
    SELECT *
    FROM iceberg_scan('%s/dim_channels', allow_moved_paths = true)
    WHERE study_id = %d
  ", warehouse, study_id)
  
  if (!is.null(sensor_type)) {
    query <- paste0(query, sprintf(" AND sensor_type = '%s'", sensor_type))
  }
  
  result <- DBI::dbGetQuery(con, query)
  return(result)
}

# ============================================================================
# Raw/Processed Data Fetchers
# ============================================================================

#' Fetch Time Series Data from Iceberg
#'
#' Retrieves time series data (raw_data or processed_data) with filtering options.
#'
#' @param con DBI connection to Iceberg.
#' @param table_name Table to query ("raw_data" or "processed_data").
#' @param study_id Study ID.
#' @param subject_id Subject ID.
#' @param session_id Session ID.
#' @param sensor_type Sensor type filter (optional).
#' @return Data frame with columns: time, channel, value, unit.
#' @keywords internal
.fetchTimeSeriesData <- function(con, table_name = "raw_data",
                                  study_id, subject_id, session_id,
                                  sensor_type = NULL) {
  warehouse <- attr(con, "iceberg_warehouse")
  
  query <- sprintf("
    SELECT time, channel, value, unit
    FROM iceberg_scan('%s/%s', allow_moved_paths = true)
    WHERE study_id = %d
      AND subject_id = '%s'
      AND session_id = '%s'
  ", warehouse, table_name, study_id, subject_id, session_id)
  
  if (!is.null(sensor_type)) {
    query <- paste0(query, sprintf(" AND sensor_type = '%s'", sensor_type))
  }
  
  query <- paste0(query, " ORDER BY time")
  
  result <- DBI::dbGetQuery(con, query)
  return(result)
}

#' Fetch OpenSim Analysis Results
#'
#' Retrieves osim_value data with joint angles, muscle activations, etc.
#'
#' @param con DBI connection to Iceberg.
#' @param run_id Run ID from analysis_runs.
#' @param session_id Session ID (optional).
#' @return Data frame with time series results from OpenSim.
#' @keywords internal
.fetchOsimResults <- function(con, run_id, session_id = NULL) {
  warehouse <- attr(con, "iceberg_warehouse")
  
  query <- sprintf("
    SELECT
      d.time,
      d.variable as channel,
      d.value,
      d.run_id,
      o.unit,
      o.output_name
    FROM iceberg_scan('%s/osim_value', allow_moved_paths = true) AS d
    LEFT JOIN iceberg_scan('%s/osim_output', allow_moved_paths = true) AS o
      ON d.output_id = o.output_id AND d.method_id = o.method_id
    WHERE d.run_id = '%s'
  ", warehouse, warehouse, run_id)
  
  if (!is.null(session_id)) {
    query <- paste0(query, sprintf(" AND d.session_id = '%s'", session_id))
  }
  
  query <- paste0(query, " ORDER BY d.time")
  
  result <- DBI::dbGetQuery(con, query)
  return(result)
}

# ============================================================================
# PhysioExperiment Constructor
# ============================================================================

#' Create PhysioExperiment Object from Iceberg Data
#'
#' Constructs a PhysioExperiment object by fetching metadata and time series data
#' from Iceberg tables.
#'
#' @param con DBI connection to Iceberg from connectToIceberg().
#' @param study_id Study ID.
#' @param subject_id Subject ID.
#' @param session_id Session ID.
#' @param table_name Data table to use ("raw_data" or "processed_data").
#' @param sensor_type Filter by sensor type (e.g., "Surface-EMG"). If NULL, all sensors included.
#' @param include_osim Include OpenSim results? (requires run_id). Default: FALSE.
#' @param run_id Run ID for OpenSim results (required if include_osim = TRUE).
#'
#' @return A PhysioExperiment object with:
#'   - assays: Matrix of signal values (time points × channels).
#'   - colData: Channel metadata (label, type, unit).
#'   - rowData: Time information.
#'   - metadata: Study, subject, session, device information.
#'   - samplingRate: Sampling rate in Hz.
#'
#' @export
#' @examples
#' \dontrun{
#' # Connect to Iceberg
#' con <- connectToIceberg()
#'
#' # Load raw EMG data
#' pe <- fetchPhysioExperiment(
#'   con = con,
#'   study_id = 1,
#'   subject_id = 101,
#'   session_id = "S001",
#'   sensor_type = "Surface-EMG",
#'   table_name = "raw_data"
#' )
#'
#' # Inspect the object
#' head(pe)
#' samplingRate(pe)
#' colnames(pe)
#'
#' # Disconnect
#' disconnectFromIceberg(con)
#' }
fetchPhysioExperiment <- function(con,
                                   study_id,
                                   subject_id,
                                   session_id,
                                   table_name = "raw_data",
                                   sensor_type = NULL,
                                   include_osim = FALSE,
                                   run_id = NULL) {
  
  # Validate inputs
  if (!inherits(con, "DBIConnection")) {
    stop("con must be a DBI connection", call. = FALSE)
  }
  
  if (include_osim && is.null(run_id)) {
    stop("run_id required when include_osim = TRUE", call. = FALSE)
  }
  
  if (!(table_name %in% c("raw_data", "processed_data"))) {
    stop("table_name must be 'raw_data' or 'processed_data'", call. = FALSE)
  }
  
  # Fetch metadata
  message("Fetching subject metadata...")
  subject_df <- .fetchSubjectMetadata(con, study_id = study_id, subject_id = subject_id)
  if (nrow(subject_df) == 0) {
    stop("No subject found for study_id=", study_id, ", subject_id=", subject_id, call. = FALSE)
  }
  
  message("Fetching device information...")
  device_df <- .fetchDeviceInfo(con, study_id = study_id)
  
  message("Fetching channel mapping...")
  channel_mapping <- .fetchChannelMapping(con, study_id = study_id, sensor_type = sensor_type)
  
  # Fetch time series data
  message("Fetching time series data from ", table_name, "...")
  ts_data <- .fetchTimeSeriesData(con,
                                    table_name = table_name,
                                    study_id = study_id,
                                    subject_id = subject_id,
                                    session_id = session_id,
                                    sensor_type = sensor_type)
  
  if (nrow(ts_data) == 0) {
    stop("No time series data found for the specified filters", call. = FALSE)
  }
  
  # Get unique channels and times
  channels <- unique(ts_data$channel)
  times <- sort(unique(ts_data$time))
  n_samples <- length(times)
  n_channels <- length(channels)
  
  # Pivot data into matrix format (n_samples × n_channels)
  message("Formatting data into matrix...")
  assay_matrix <- matrix(NA_real_, nrow = n_samples, ncol = n_channels)
  colnames(assay_matrix) <- channels
  
  for (i in seq_along(channels)) {
    ch_data <- subset(ts_data, ts_data$channel == channels[i])
    # Match times to matrix rows
    time_indices <- match(ch_data$time, times)
    assay_matrix[time_indices, i] <- ch_data$value
  }
  
  # Build colData (channel metadata)
  # PhysioExperiment uses 'label' for channelNames() and 'type' for getChannelsByType()
  col_df <- data.frame(
    label = channels,
    stringsAsFactors = FALSE
  )
  
  # Add unit information if available
  if (nrow(ts_data) > 0) {
    unit_info <- unique(ts_data[, c("channel", "unit")])
    names(unit_info)[1] <- "label"
    col_df <- merge(col_df, unit_info, by = "label", all.x = TRUE)
  }
  
  # Add sensor type from channel mapping if available
  if (nrow(channel_mapping) > 0) {
    type_info <- unique(channel_mapping[, c("column_name", "sensor_type")])
    names(type_info) <- c("label", "type")
    col_df <- merge(col_df, type_info, by = "label", all.x = TRUE)
  }
  
  # Build rowData (time information)
  row_df <- data.frame(
    time = times,
    time_index = seq_len(n_samples),
    stringsAsFactors = FALSE
  )
  
  # Determine sampling rate
  if (nrow(device_df) > 0) {
    sampling_rate <- device_df$samplingrate_Hz[1]
  } else {
    # Estimate from time series if device info not available
    if (n_samples > 1) {
      sampling_rate <- 1 / mean(diff(times))
    } else {
      sampling_rate <- NA_real_
    }
  }
  
  # Always start with measured data as the primary assay
  assay_list <- list(measured = assay_matrix)

  # OpenSim results stored as a separate PE object in metadata
  # because osim data typically has different channels (muscles/joints)
  # and SummarizedExperiment requires all assays to share the same dimensions.
  osim_pe <- NULL
  if (include_osim) {
    message("Fetching OpenSim results...")
    osim_results <- .fetchOsimResults(con, run_id = run_id, session_id = session_id)
    
    if (nrow(osim_results) > 0) {
      osim_channels <- unique(osim_results$channel)
      osim_times <- sort(unique(osim_results$time))
      osim_matrix <- matrix(NA_real_, nrow = length(osim_times), ncol = length(osim_channels))
      colnames(osim_matrix) <- osim_channels
      
      for (i in seq_along(osim_channels)) {
        ch_data <- subset(osim_results, osim_results$channel == osim_channels[i])
        time_indices <- match(ch_data$time, osim_times)
        osim_matrix[time_indices, i] <- ch_data$value
      }
      
      # Build separate PhysioExperiment for OpenSim data
      osim_col_df <- data.frame(
        label = osim_channels,
        type = rep("OpenSim", length(osim_channels)),
        stringsAsFactors = FALSE
      )
      osim_row_df <- data.frame(
        time = osim_times,
        time_index = seq_along(osim_times),
        stringsAsFactors = FALSE
      )
      osim_sr <- if (length(osim_times) > 1) 1 / mean(diff(osim_times)) else NA_real_
      osim_pe <- PhysioExperiment::PhysioExperiment(
        assays = list(osim_value = osim_matrix),
        rowData = S4Vectors::DataFrame(osim_row_df),
        colData = S4Vectors::DataFrame(osim_col_df),
        metadata = list(run_id = run_id, source = "OpenSim"),
        samplingRate = osim_sr
      )
      message("OpenSim PhysioExperiment: ", length(osim_times), " samples, ",
              length(osim_channels), " variables")
    }
  }
  
  # Build metadata list
  metadata_list <- list(
    study_id = study_id,
    subject_id = subject_id,
    session_id = session_id,
    table_name = table_name,
    run_id = if (include_osim) run_id else NA,
    subject_height_m = subject_df$height_m[1],
    subject_weight_kg = subject_df$weight_kg[1],
    subject_sex = subject_df$sex[1],
    subject_age_years = subject_df$age_year[1],
    data_fetch_timestamp = Sys.time()
  )
  
  # Attach OpenSim PE as metadata so it can be retrieved with:
  #   osim_pe <- metadata(pe)$osim_experiment
  if (!is.null(osim_pe)) {
    metadata_list$osim_experiment <- osim_pe
  }
  
  # Create PhysioExperiment object
  message("Creating PhysioExperiment object...")
  pe <- PhysioExperiment::PhysioExperiment(
    assays = assay_list,
    rowData = S4Vectors::DataFrame(row_df),
    colData = S4Vectors::DataFrame(col_df),
    metadata = metadata_list,
    samplingRate = sampling_rate
  )
  
  message("Successfully created PhysioExperiment object with ",
          n_samples, " samples and ", n_channels, " channels")
  
  return(pe)
}

# ============================================================================
# Batch Fetcher for Multiple Sessions
# ============================================================================

#' Fetch Multiple Sessions as List of PhysioExperiment Objects
#'
#' Convenience function to load data for multiple sessions in a single call.
#'
#' @param con DBI connection to Iceberg.
#' @param study_id Study ID.
#' @param subject_id Subject ID.
#' @param session_ids Vector of session IDs.
#' @param table_name Data table ("raw_data" or "processed_data").
#' @param sensor_type Sensor type filter (optional).
#' @return List of PhysioExperiment objects named by session_id.
#' @export
fetchPhysioExperimentBatch <- function(con,
                                        study_id,
                                        subject_id,
                                        session_ids,
                                        table_name = "raw_data",
                                        sensor_type = NULL) {
  
  pe_list <- list()
  
  for (sid in session_ids) {
    tryCatch({
      message("\n--- Processing session: ", sid, " ---")
      pe <- fetchPhysioExperiment(
        con = con,
        study_id = study_id,
        subject_id = subject_id,
        session_id = sid,
        table_name = table_name,
        sensor_type = sensor_type,
        include_osim = FALSE
      )
      pe_list[[sid]] <- pe
    }, error = function(e) {
      message("Error loading session ", sid, ": ", e$message)
    })
  }
  
  if (length(pe_list) == 0) {
    warning("No sessions were successfully loaded")
  }
  
  return(pe_list)
}

# ============================================================================
# Helper: List Available Sessions
# ============================================================================

#' List Available Sessions for a Subject
#'
#' @param con DBI connection to Iceberg.
#' @param study_id Study ID.
#' @param subject_id Subject ID (optional).
#' @return Data frame with available sessions.
#' @export
listAvailableSessions <- function(con, study_id, subject_id = NULL) {
  warehouse <- attr(con, "iceberg_warehouse")
  
  query <- sprintf("
    SELECT DISTINCT study_id, subject_id, session_id
    FROM iceberg_scan('%s/session_info', allow_moved_paths = true)
    WHERE study_id = %d
  ", warehouse, study_id)
  
  if (!is.null(subject_id)) {
    query <- paste0(query, sprintf(" AND subject_id = %d", subject_id))
  }
  
  query <- paste0(query, " ORDER BY subject_id, session_id")
  
  result <- DBI::dbGetQuery(con, query)
  return(result)
}

# ============================================================================
# OpenSim-specific Fetcher
# ============================================================================

#' Fetch OpenSim Results as a PhysioExperiment Object
#'
#' Convenience function that directly creates a PhysioExperiment from OpenSim
#' analysis results (osim_value table). This is the primary bridge function for
#' downstream analysis of muscle activations, joint angles, etc.
#'
#' @param con DBI connection to Iceberg from connectToIceberg().
#' @param run_id Run ID from analysis_runs.
#' @param study_id Study ID.
#' @param subject_id Subject ID.
#' @param session_id Session ID (optional filter).
#' @param variable_filter SQL LIKE pattern to filter variables
#'   (e.g. "\%activation\%" for muscle activations only). NULL = all variables.
#' @return A PhysioExperiment with osim_value data as the primary assay.
#'
#' @export
#' @examples
#' \dontrun{
#' con <- connectToIceberg()
#'
#' # All OpenSim outputs for a run
#' pe_osim <- fetchOsimPhysioExperiment(con, run_id = "20260209_0223_default",
#'                                       study_id = 31, subject_id = 1)
#'
#' # Only muscle activations
#' pe_act <- fetchOsimPhysioExperiment(con, run_id = "20260209_0223_default",
#'                                      study_id = 31, subject_id = 1,
#'                                      variable_filter = "%activation%")
#'
#' # Use PhysioExperiment tools for downstream analysis
#' plotMultiChannel(pe_act, style = "butterfly")
#'
#' disconnectFromIceberg(con)
#' }
fetchOsimPhysioExperiment <- function(con,
                                       run_id,
                                       study_id,
                                       subject_id,
                                       session_id = NULL,
                                       variable_filter = NULL) {
  
  if (!inherits(con, "DBIConnection")) {
    stop("con must be a DBI connection", call. = FALSE)
  }
  warehouse <- attr(con, "iceberg_warehouse")
  
  # Build query with optional filters
  query <- sprintf("
    SELECT
      d.time,
      d.variable AS channel,
      d.value,
      o.unit,
      o.output_name
    FROM iceberg_scan('%s/osim_value', allow_moved_paths = true) AS d
    LEFT JOIN iceberg_scan('%s/osim_output', allow_moved_paths = true) AS o
      ON d.output_id = o.output_id AND d.method_id = o.method_id
    WHERE d.run_id = '%s'
      AND d.study_id = %d
      AND d.subject_id = '%s'
  ", warehouse, warehouse, run_id, study_id, subject_id)
  
  if (!is.null(session_id)) {
    query <- paste0(query, sprintf(" AND d.session_id = '%s'", session_id))
  }
  if (!is.null(variable_filter)) {
    query <- paste0(query, sprintf(" AND d.variable LIKE '%s'", variable_filter))
  }
  
  query <- paste0(query, " ORDER BY d.time, d.variable")
  
  message("Fetching OpenSim results...")
  osim_df <- DBI::dbGetQuery(con, query)
  
  if (nrow(osim_df) == 0) {
    stop("No OpenSim data found for run_id=", run_id,
         ", study_id=", study_id, ", subject_id=", subject_id, call. = FALSE)
  }
  
  # Pivot to matrix
  channels <- unique(osim_df$channel)
  times <- sort(unique(osim_df$time))
  n_samples <- length(times)
  n_channels <- length(channels)
  
  signal_matrix <- matrix(NA_real_, nrow = n_samples, ncol = n_channels)
  colnames(signal_matrix) <- channels
  
  for (i in seq_along(channels)) {
    ch_data <- subset(osim_df, osim_df$channel == channels[i])
    time_indices <- match(ch_data$time, times)
    signal_matrix[time_indices, i] <- ch_data$value
  }
  
  # Build colData with PE conventions
  unit_info <- unique(osim_df[, c("channel", "unit")])
  col_df <- data.frame(
    label = channels,
    type = rep("OpenSim", n_channels),
    stringsAsFactors = FALSE
  )
  names(unit_info)[1] <- "label"
  col_df <- merge(col_df, unit_info, by = "label", all.x = TRUE)
  
  # rowData
  row_df <- data.frame(
    time = times,
    time_index = seq_len(n_samples),
    stringsAsFactors = FALSE
  )
  
  # Sampling rate from time vector
  sampling_rate <- if (n_samples > 1) 1 / mean(diff(times)) else NA_real_
  
  # Subject metadata
  subject_df <- .fetchSubjectMetadata(con, study_id = study_id, subject_id = subject_id)
  
  metadata_list <- list(
    study_id = study_id,
    subject_id = subject_id,
    session_id = session_id,
    run_id = run_id,
    source = "OpenSim",
    variable_filter = variable_filter,
    data_fetch_timestamp = Sys.time()
  )
  if (nrow(subject_df) > 0) {
    metadata_list$subject_height_m <- subject_df$height_m[1]
    metadata_list$subject_weight_kg <- subject_df$weight_kg[1]
    metadata_list$subject_sex <- subject_df$sex[1]
    metadata_list$subject_age_years <- subject_df$age_year[1]
  }
  
  pe <- PhysioExperiment::PhysioExperiment(
    assays = list(osim_value = signal_matrix),
    rowData = S4Vectors::DataFrame(row_df),
    colData = S4Vectors::DataFrame(col_df),
    metadata = metadata_list,
    samplingRate = sampling_rate
  )
  
  message("Created OpenSim PhysioExperiment: ",
          n_samples, " time points × ", n_channels, " variables")
  return(pe)
}

# ============================================================================
# Helper: List Available Studies
# ============================================================================

#' List Available Studies from study_table
#'
#' @param con DBI connection to Iceberg.
#' @return Data frame with available studies.
#' @export
listAvailableStudies <- function(con) {
  warehouse <- attr(con, "iceberg_warehouse")
  
  query <- sprintf("
    SELECT *
    FROM iceberg_scan('%s/study_table', allow_moved_paths = true)
    ORDER BY study_id
  ", warehouse)
  
  result <- DBI::dbGetQuery(con, query)
  return(result)
}
