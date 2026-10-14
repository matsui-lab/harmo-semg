args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3L) stop("request, output and R library required")
if (nzchar(args[3])) .libPaths(c(strsplit(args[3], .Platform$path.sep, fixed=TRUE)[[1]], .libPaths()))
request <- jsonlite::read_json(args[1], simplifyVector=TRUE)
suppressPackageStartupMessages(library(PhysioIO))
if (request$repository == "figshare") {
  info <- PhysioIO::downloadFigshare(request$id, dest=args[2], files=request$name,
    version=request$version, verify_md5=TRUE, overwrite=FALSE)
} else if (request$repository == "zenodo") {
  info <- PhysioIO::downloadZenodo(request$id, dest=args[2], files=request$name,
    verify_md5=TRUE, overwrite=FALSE)
} else stop("Unsupported repository")
if (nrow(info) != 1L || info$name[[1]] != request$name) stop("Unexpected returned file selection")
jsonlite::write_json(list(request=request, files=info,
  PhysioIO=as.character(packageVersion("PhysioIO"))),
  file.path(args[2], "acquisition.json"), auto_unbox=TRUE, pretty=TRUE, digits=NA)
