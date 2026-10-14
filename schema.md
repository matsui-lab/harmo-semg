# Iceberg Tables Schema & Roles

This document summarizes the definition and role of Iceberg tables used in the OpenSim analysis pipeline.

## 1. Master Data
Tables managing experimental settings, subject information, sensor definitions, etc.

### `study_table`
Metadata for the entire research project.
*   **Role**: Associates study IDs with datasets.
*   **Schema**:
    *   `study_id` (Long): Study ID
    *   `dataset_name` (String): Dataset name
    *   `doi` (String): DOI
    *   `lab` (String): Lab name
    *   `notes` (String): Notes
    *   `date` (String): Date

### `subject_table`
Subject attribute information.
*   **Role**: Provides height, weight, etc. necessary for Scaling.
*   **Schema**:
    *   `study_id` (Long): Study ID
    *   `subject_id` (Long): Subject ID
    *   `height_m` (Double): Height (m)
    *   `weight_kg` (Double): Weight (kg)
    *   `age_year` (Long): Age
    *   `sex` (String): Sex
    *   `pathlogy` (String): Pathology information
    *   `bmi` (Double): BMI
    *   `notes` (Double): Notes

### `session_info`
Definition of sessions and tasks.
*   **Role**: Identifies trials (tasks) for analysis. `task_id=0` is treated as a Static trial.
*   **Schema**:
    *   `study_id` (Long): Study ID
    *   `subject_id` (Long): Subject ID
    *   `session_id` (String): Session ID
    *   `task_id` (Double): Task ID (0=Static)
    *   `notes` (String): Notes

### `device_table`
Measurement device configuration information.
*   **Role**: Manages device specifications such as sampling rates.
*   **Schema**:
    *   `study_id` (Long): Study ID
    *   `device_id` (Long): Device ID
    *   `device_name` (String): Device name
    *   `type` (String): Sensor type (Motion-capture, Surface-EMG, etc.)
    *   `samplingrate_Hz` (Long): Sampling frequency
    *   `manufacture` (String): Manufacturer
    *   `notes` (String): Notes

### `dim_channels`
Channel mapping definition.
*   **Role**: Mapping between EMG channel names and OpenSim muscle names, or GRF channel definitions.
*   **Schema**:
    *   `study_id` (Long): Study ID
    *   `column_name` (String): Original data column name
    *   `muscle` (String): Corresponding muscle name (SENIAM/OpenSim name)
    *   `side` (String): Side (r/l)
    *   `sensor_type` (String): Sensor type
    *   `physical_quantity` (String): Physical quantity
    *   `unit` (String): Unit
    *   `data_type` (String): Data type

### `dim_marker`
Marker set definition.
*   **Role**: Marker names, body parts, weighting definitions for Scaling/IK.
*   **Schema**:
    *   `study_id` (Long): Study ID
    *   `marker` (String): Marker name
    *   `body` (String): Body part
    *   `location` (String): Location
    *   `scale_apply` (Boolean): Whether to use for Scaling
    *   `scale_weight` (Long): Weight during Scaling
    *   `ik_apply` (Boolean): Whether to use for IK
    *   `ik_weight` (Long): Weight during IK

### `dim_coordinate`
Coordinate system definition.
*   **Role**: Transformation rules from laboratory coordinate system to OpenSim coordinate system.
*   **Schema**:
    *   `study_id` (Long): Study ID
    *   `sensor_type` (String): Sensor type
    *   `coord_sys_id` (String): Coordinate system ID
    *   `x_positive`, `y_positive`, `z_positive` (String): Positive direction definition for each axis
    *   `reference_frame` (String): Reference frame
    *   `physical_quantity` (String): Physical quantity
    *   `note` (String): Note

---

## 2. Raw Data
Original time-series data obtained from measurement devices.

### `raw_data`
Raw data for all sensors (Long format).
*   **Role**: Primary data serving as input for analysis.
*   **Schema**:
    *   `study_id` (Integer): Study ID
    *   `subject_id` (String): Subject ID
    *   `session_id` (String): Session ID
    *   `sensor_type` (String): Sensor type
    *   `time` (Double): Time
    *   `channel` (String): Channel name
    *   `value` (Double): Value
    *   `unit` (String): Unit

### `processed_data`
Pre-processed data.
*   **Role**: Data that has undergone filtering or normalization.
*   **Schema**:
    *   Same columns as `raw_data` + `processed_id` (Integer)

---

## 3. Provenance & Configuration Data
Tables for recording "what parameters were used for analysis" to ensure reproducibility. Managed with `run_id` as the key.

### `analysis_runs`
Parent record for analysis execution.
*   **Role**: Records basic information (OpenSim version, etc.) for each batch run.
*   **Schema**:
    *   `run_id` (String): Execution ID
    *   `study_id` (Long): Study ID
    *   `opensim_version` (String): OpenSim version
    *   `created_at` (Timestamp): Execution date and time
    *   `basemodel_id` (String): Base model ID
    *   `studymodel_path` (String): Study model path

### `workflow_steps`
Executed workflow steps.
*   **Role**: Which tools (Scaling, IK, Moco, etc.) were executed in what order.
*   **Schema**:
    *   `run_id` (String): Execution ID
    *   `step_order` (Long): Order
    *   `step_type` (String): Step name (scale, ik, moco, etc.)
    *   `method_id` (Long): Method ID

### `marker_task_parameters`
Marker settings during IK/Scaling.
*   **Role**: Weights and enabled/disabled status of markers actually used.
*   **Schema**:
    *   `run_id` (String): Execution ID
    *   `step_type` (String): Step (scale/ik)
    *   `target_name` (String): Marker name
    *   `weight` (Long): Weight
    *   `is_enabled` (Boolean): Enabled flag

### `muscle_parameters`
Muscle parameters during Moco analysis.
*   **Role**: EMG tracking weights, muscle activity boundary settings, etc.
*   **Schema**:
    *   `run_id` (String): Execution ID
    *   `step_type` (String): Step
    *   `Moco_emg_path` (String): Muscle path on OpenSim
    *   `Moco_emg_name` (String): EMG name
    *   `Moco_emg_column` (String): EMG column name
    *   `emg_tracking_weight` (Double): Tracking weight
    *   `Moco_emg_boundsMin`, `Moco_emg_boundsMax` (Double): Muscle activity boundary values
    *   `constraint_tolerance` (Double): Constraint tolerance
    *   `convergence_tolerance` (Double): Convergence tolerance

### `session_specific_parameters`
Variable parameters per trial.
*   **Role**: Settings that differ per session, such as analysis time range and mesh interval.
*   **Schema**:
    *   `run_id` (String): Execution ID
    *   `session_id` (String): Session ID
    *   `study_id` (String): Study ID
    *   `subject_id` (String): Subject ID
    *   `Moco_start_time`, `Moco_end_time` (Double): Analysis range
    *   `mesh_interval` (Double): Mesh interval
    *   `subj_model_path` (String): Used model path

### `osim_input_header` / `osim_input_value`
Snapshot of input files (.trc/.mot) passed to analysis.
*   **Role**: Record of input data actually passed to OpenSim during analysis.

---

## 4. Analysis Results Data
Calculation results by OpenSim and their quality evaluation data.

### `osim_header`
Header information of output files (.sto/.mot).
*   **Role**: Metadata of result data (number of rows, columns, states, etc.).
*   **Schema**:
    *   `run_id` (String): Execution ID
    *   `session_id` (String): Session ID
    *   `FileType` (String): File type
    *   `nRows`, `nColumns` (Long): Number of rows/columns
    *   `objective` (Double): Objective function value
    *   `success` (String): Success status
    *   `status` (String): Status
    *   `solver_duration` (Double): Solver duration
    *   `objective_emg_tracking` (Double): EMG tracking term
    *   `objective_excitation_effort` (Double): Effort term

### `osim_value`
Time-series numerical data of analysis results.
*   **Role**: Main body of calculation results such as joint angles, moments, muscle activities.
*   **Schema**:
    *   `run_id` (String): Execution ID
    *   `session_id` (String): Session ID
    *   `time` (Double): Time
    *   `variable` (String): Variable name
    *   `value` (Double): Value
    *   `output_id`, `method_id`: Reference IDs

### `osim_output`
Definition master for output variables.
*   **Role**: Definition of what `variable` in `osim_value` means (unit, physical quantity).
*   **Schema**:
    *   `output_name` (String): Variable name
    *   `unit` (String): Unit
    *   `physical_quantity` (String): Physical quantity
    *   `notes` (String): Description
    *   `supports_quantity` (Boolean): Flag

### `osim_validation`
Quality metrics (summary) of analysis results.
*   **Role**: Accuracy evaluation per trial.
*   **Schema**:
    *   `run_id` (String): Execution ID
    *   `session_id` (String): Session ID
    *   `moco_success` (Boolean): Moco convergence success flag
    *   `moco_objective` (Double): Objective function value
    *   `max_residual_force` (Double): Maximum residual force
    *   `avg_residual_force` (Double): Average residual force
    *   `max_reserve_ratio` (Double): Maximum reserve force ratio
    *   `quality_status` (String): Quality status
    *   *(Note: `dtw_avg_normalized_distance` is also added in code logic)*

### `emg_tracking_metrics`
Details of EMG tracking accuracy per muscle.
*   **Role**: Detailed analysis for which muscle prediction accuracy was good/bad.
*   **Schema (Code-based)**:
    *   `run_id` (String)
    *   `session_id` (String)
    *   `muscle_path` (String): Muscle path
    *   `emg_column` (String): EMG column name
    *   `dtw_distance` (Double): DTW distance
    *   `dtw_normalized_distance` (Double): Normalized DTW distance