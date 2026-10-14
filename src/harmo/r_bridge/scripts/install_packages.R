# Install required packages for harm2physio analysis pipeline
# This script installs all required R packages
# Run: Rscript install_packages.R

cat("Installing required packages...\n")

# List of required packages
packages <- c(
  "duckdb",           # DuckDB R package for data extraction
  "DBI",              # Database interface
  "dplyr",            # Data manipulation
  "tidyr",            # Data reshaping
  "ggplot2",          # Visualization
  "reshape2",         # Data reshaping for heatmaps
  "NMF",              # Non-negative matrix factorization (synergy analysis)
  "jsonlite",         # JSON parsing (CLI parameter exchange)
  "signal",           # Signal processing
  "wavelets",         # Wavelet analysis
  "Matrix",           # Matrix operations
  "data.table"        # Fast data manipulation
)

# BiocManager packages (if using PhysioExperiment from Bioconductor)
bioc_packages <- c(
  "SummarizedExperiment",   # Base class for PhysioExperiment
  "S4Vectors",              # S4 infrastructure
  "DelayedArray",           # For large data arrays
  "HDF5Array",              # HDF5 support (if needed)
  "BiocParallel"            # Parallel processing
)

# Install CRAN packages
for (pkg in packages) {
  if (!require(pkg, character.only = TRUE)) {
    cat(sprintf("Installing %s...\n", pkg))
    install.packages(pkg, dependencies = TRUE)
  } else {
    cat(sprintf("%s is already installed.\n", pkg))
  }
}

# Install Bioconductor packages
if (!require("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager")
}

for (pkg in bioc_packages) {
  if (!require(pkg, character.only = TRUE)) {
    cat(sprintf("Installing Bioconductor package: %s...\n", pkg))
    BiocManager::install(pkg)
  } else {
    cat(sprintf("%s is already installed.\n", pkg))
  }
}

# Optional: Install PhysioExperiment if available
cat("\nNote: PhysioExperiment package should be installed separately.\n")
cat("To install PhysioExperiment from the local repository:\n")
cat("  devtools::install_local('/home/ota/PhysioExperiment')\n")

cat("\nPackage installation complete!\n")
