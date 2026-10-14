args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3L) stop("record, output directory, and R library required")
if (nzchar(args[3])) .libPaths(c(strsplit(args[3], .Platform$path.sep, fixed=TRUE)[[1]], .libPaths()))
suppressPackageStartupMessages(library(PhysioIO))
if (!"readWFDB" %in% getNamespaceExports("PhysioIO")) stop("PhysioIO with readWFDB required")
pe <- PhysioIO::readWFDB(args[1])
x <- as.matrix(SummarizedExperiment::assay(pe, "raw"))
cd <- as.data.frame(SummarizedExperiment::colData(pe))
con <- file(file.path(args[2], "samples.f64"), "wb")
writeBin(as.double(x), con, size = 8L, endian = "little")
close(con)
jsonlite::write_json(list(shape = dim(x), labels = as.character(cd$label),
  units = as.character(cd$unit), sampling_rate_hz = PhysioCore::samplingRate(pe),
  PhysioIO = as.character(packageVersion("PhysioIO")),
  PhysioCore = as.character(packageVersion("PhysioCore")),
  reader_sha256 = digest::digest(paste(deparse(PhysioIO::readWFDB), collapse="\n"), algo="sha256", serialize=FALSE)),
  file.path(args[2], "reader.json"), auto_unbox = TRUE, digits = NA, pretty = TRUE)
