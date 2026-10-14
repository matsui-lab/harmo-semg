# ============================================================================
# Usage Examples: Loading Iceberg Data into PhysioExperiment (RStudio Server)
# ============================================================================
#
# This script demonstrates how to use the iceberg_to_physio.R functions
# to load data from Apache Iceberg into PhysioExperiment objects.
#
# Prerequisites:
#   - Source the iceberg_to_physio.R file
#   - Iceberg warehouse must be available at: warehouse/warehouse/
#   - PhysioExperiment package installed and loaded
#
# For RStudio Server: Copy this file to your session and source it,
# or run each example interactively.

# Before running: source the function definitions
# source("iceberg_to_physio.R")

# ============================================================================
# Example 1: Connect and Explore Available Data
# ============================================================================
source("script/iceberg_to_physio.R")
# 一時的な接続を作成
#tmp_con <- DBI::dbConnect(duckdb::duckdb())
# エラーで指摘された 'avro' をインストール
#DBI::dbExecute(tmp_con, "INSTALL avro; LOAD avro;")
# ついでに iceberg も最新の状態に更新
#DBI::dbExecute(tmp_con, "INSTALL iceberg; LOAD iceberg;")
# 接続を閉じる
#DBI::dbDisconnect(tmp_con)

# Connect to the Iceberg warehouse
con <- connectToIceberg()

# List all available studies
studies <- listAvailableStudies(con)
print(studies)
# View columns of the first study
head(studies, 3)

# List available sessions for a specific study
sessions <- listAvailableSessions(con, study_id = 31)
print(sessions)

# Or filter by both study and subject
sessions_by_subject <- listAvailableSessions(con, study_id = 31, subject_id = 1)
print(sessions_by_subject)

# ============================================================================
# Example 2: Load Raw EMG Data for a Single Trial
# ============================================================================

# Fetch raw EMG data from a specific trial
pe_emg <- fetchPhysioExperiment(
  con = con,
  study_id = 31,
  subject_id = 1,
  session_id = "ses-V3-T5",
  table_name = "raw_data",
  sensor_type = "Surface-EMG"
)

# Inspect the PhysioExperiment object
dim(pe_emg)  # [samples, channels]
samplingRate(pe_emg)
colnames(pe_emg)
head(colData(pe_emg))

# Extract the assay (signal matrix)
signal_matrix <- assay(pe_emg)
dim(signal_matrix)

# Extract metadata
metadata <- metadata(pe_emg)
metadata$subject_weight_kg
metadata$data_fetch_timestamp

# ============================================================================
# Example 3: Load Processed Data
# ============================================================================

# Fetch pre-processed data (e.g., filtered, normalized)
#time列がNanだから、エラーが出ているっぽい。（すでに時間正規化されているデータで、元々time列が欠損している）
pe_processed <- fetchPhysioExperiment(
  con = con,
  study_id = 31,
  subject_id = 1,
  session_id = "ses-V3-T5-R_processed",
  table_name = "processed_data",
  sensor_type = "Surface-EMG"
)

# Compare sampling rates
samplingRate(pe_emg)
samplingRate(pe_processed)

# ============================================================================
# Example 4: Load All Sensor Types (No Filter)
# ============================================================================

# Fetch all available sensors for the session
#エラーの発生
#Error in `rownames<-`(`*tmp*`, value = .get_colnames_from_first_assay(assays)) : invalid rownames length
pe_all_sensors <- fetchPhysioExperiment(
  con = con,
  study_id = 31,
  subject_id = 1,
  session_id = "ses-V3-T5",
  table_name = "raw_data",
  sensor_type = NULL  # No filter = all sensors
)

# See what was loaded
dim(pe_all_sensors)
unique(colData(pe_all_sensors)$sensor_type)

# ============================================================================
# Example 5: Load Multiple Sessions for the Same Subject
# ============================================================================

# Fetch all sessions for subject 101, study 1
available_sessions <- listAvailableSessions(con, study_id = 31, subject_id = 1)
session_list <- available_sessions$session_id

# Load all sessions at once
pe_batch <- fetchPhysioExperimentBatch(
  con = con,
  study_id = 31,
  subject_id = 1,
  session_ids = session_list,
  table_name = "raw_data",
  sensor_type = "Surface-EMG"
)

# Access each session
names(pe_batch)  # Session IDs
pe_session_01 <- pe_batch[["ses-V3-T5"]]
pe_session_02 <- pe_batch[["ses-V3-T6"]]

# Compare properties
samplingRate(pe_session_01)
samplingRate(pe_session_02)

# ============================================================================
# Example 6: Load OpenSim Analysis Results
# ============================================================================

# If OpenSim results are available, load them along with measured data
#エラー
#Error in method(object) : all assays must have the same nrow and ncol
pe_with_osim <- fetchPhysioExperiment(
  con = con,
  study_id = 31,
  subject_id = 1,
  session_id = "ses-V3-T5",
  table_name = "raw_data",
  sensor_type = "Surface-EMG",
  include_osim = TRUE,
  run_id = "20260209_0223_default"  # Your OpenSim run ID
)

# The object now contains two assays
names(assays(pe_with_osim))
# [1] "measured"  "OpenSim"

# Access measured data
measured_data <- assay(pe_with_osim, "measured")
dim(measured_data)

# Access OpenSim results
osim_data <- assay(pe_with_osim, "OpenSim")
dim(osim_data)
colnames(osim_data)  # Joint angles, muscle activations, etc.

# ============================================================================
# Example 7: Work with PhysioExperiment Methods
# ============================================================================

# Assuming pe_emg is loaded from Example 2
pe <- pe_emg

# Get signal statistics
cat("Signal Summary:\n")
cat("  Channels: ", ncol(pe), "\n")
cat("  Samples: ", nrow(pe), "\n")
cat("  Sampling Rate: ", samplingRate(pe), " Hz\n")

# Calculate duration in seconds
duration_sec <- nrow(pe) / samplingRate(pe)
cat("  Duration: ", round(duration_sec, 2), " seconds\n\n")

# View channel information
cat("Channel Information:\n")
print(colData(pe))

# Extract time vector (from rowData)
time_vector <- rowData(pe)$time
head(time_vector, 10)
tail(time_vector, 10)

# Get subject demographic info
subject_info <- metadata(pe)
cat("Subject Info:\n")
cat("  Age: ", subject_info$subject_age_years, "\n")
cat("  Sex: ", subject_info$subject_sex, "\n")
cat("  Weight: ", subject_info$subject_weight_kg, " kg\n")
cat("  Height: ", subject_info$subject_height_m, " m\n")

# ============================================================================
# Example 8: Advanced - Filter and Process
# ============================================================================

# Once loaded, you can use PhysioExperiment processing methods
# (assuming these functions exist in your PhysioExperiment installation)

# Example: Band-pass filter
# pe_filtered <- butterworthFilter(pe, lowcut = 10, highcut = 500, order = 4)

# Example: Normalize EMG
# pe_normalized <- batchNormalize(pe, method = "minmax")

# Example: Epoch the data
# pe_epoched <- averageEpochs(
#   pe,
#   event_times = seq(0, duration_sec, by = 1),  # 1-second epochs
#   baseline = c(-0.1, 0)
# )

# ============================================================================
# Clean Up
# ============================================================================

# Always disconnect when done
disconnectFromIceberg(con)

message("All examples completed. Iceberg connection closed.")

