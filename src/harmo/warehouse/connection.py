"""Single source of truth for local and REST Iceberg connections."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def get_warehouse_path(explicit: Optional[str] = None) -> str:
    """Resolve the warehouse path without changing the process environment."""
    raw = explicit or os.environ.get("HARMO_WAREHOUSE_PATH") or "./warehouse"
    if raw.startswith(("s3://", "s3a://", "gs://", "abfs://", "file://")):
        return raw.rstrip("/")
    return str(Path(raw).expanduser().resolve())


def iceberg_table_path(table_name: str, warehouse_path: Optional[str] = None) -> str:
    """Return the physical table path for a local Hadoop-style warehouse."""
    short_name = table_name.rsplit(".", 1)[-1]
    root = get_warehouse_path(warehouse_path)
    return "{}/warehouse/{}".format(root.rstrip("/"), short_name)


def get_spark_session(
    warehouse_path: Optional[str] = None,
    app_name: str = "harmo",
):
    """Create a Spark session for either REST or local Hadoop Iceberg.

    Set ``HARMO_ICEBERG_CATALOG_URI`` to select the multi-user REST catalog.
    Optional S3 settings are read from ``HARMO_S3_ENDPOINT``,
    ``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY``.
    """
    try:
        from pyspark.sql import SparkSession
    except ImportError as exc:  # pragma: no cover - exercised in full container
        raise RuntimeError(
            "PySpark is required for warehouse writes; install harmo[spark]"
        ) from exc

    warehouse = get_warehouse_path(warehouse_path)
    builder = (
        SparkSession.builder.appName(app_name)
        .master(os.environ.get("HARMO_SPARK_MASTER", "local[*]"))
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config(
            "spark.jars.packages",
            os.environ.get(
                "HARMO_ICEBERG_SPARK_PACKAGE",
                "org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:1.5.0",
            ),
        )
        .config("spark.sql.catalog.local", "org.apache.iceberg.spark.SparkCatalog")
        .config("spark.sql.defaultCatalog", "local")
        .config("spark.sql.ansi.enabled", "false")
    )

    catalog_uri = os.environ.get("HARMO_ICEBERG_CATALOG_URI")
    if catalog_uri:
        builder = (
            builder.config("spark.sql.catalog.local.type", "rest")
            .config("spark.sql.catalog.local.uri", catalog_uri)
            .config("spark.sql.catalog.local.warehouse", warehouse)
        )
        endpoint = os.environ.get("HARMO_S3_ENDPOINT")
        if endpoint:
            builder = builder.config("spark.sql.catalog.local.s3.endpoint", endpoint)
        if os.environ.get("HARMO_S3_PATH_STYLE_ACCESS", "true").lower() == "true":
            builder = builder.config("spark.sql.catalog.local.s3.path-style-access", "true")
    else:
        builder = (
            builder.config("spark.sql.catalog.local.type", "hadoop")
            .config("spark.sql.catalog.local.warehouse", warehouse)
        )
    return builder.getOrCreate()
