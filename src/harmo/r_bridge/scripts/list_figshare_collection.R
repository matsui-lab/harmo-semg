args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3L) stop("collection ID, output and R library required")
if (!grepl("^[1-9][0-9]*$", args[1])) stop("positive collection ID required")
if (nzchar(args[3])) .libPaths(c(strsplit(args[3], .Platform$path.sep, fixed=TRUE)[[1]], .libPaths()))
options(timeout=60)
suppressPackageStartupMessages(library(PhysioIO))
articles <- PhysioIO::figshareCollectionArticles(args[1])
jsonlite::write_json(list(PhysioIO=as.character(packageVersion("PhysioIO")), articles=articles),
  args[2], dataframe="rows", auto_unbox=TRUE, pretty=TRUE, na="null")
