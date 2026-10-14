import os
import tempfile
import unittest
import types
import sys
from pathlib import Path
from unittest.mock import patch

from harmo.warehouse.connection import (
    get_spark_session,
    get_warehouse_path,
    iceberg_table_path,
)


class _FakeBuilder:
    def __init__(self):
        self.values = {}

    def appName(self, value):
        self.values["appName"] = value
        return self

    def master(self, value):
        self.values["master"] = value
        return self

    def config(self, key, value):
        self.values[key] = value
        return self

    def getOrCreate(self):
        return self


def _fake_pyspark(builder):
    sql = types.ModuleType("pyspark.sql")
    sql.SparkSession = types.SimpleNamespace(builder=builder)
    pyspark = types.ModuleType("pyspark")
    pyspark.sql = sql
    return {"pyspark": pyspark, "pyspark.sql": sql}


class WarehouseConnectionTests(unittest.TestCase):
    def test_explicit_path_wins(self):
        with patch.dict(os.environ, {"HARMO_WAREHOUSE_PATH": "/wrong"}):
            self.assertEqual(get_warehouse_path("s3://bucket/root/"), "s3://bucket/root")

    def test_table_path_uses_short_name(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                iceberg_table_path("local.warehouse.raw_data", directory),
                str(Path(directory).resolve() / "warehouse" / "raw_data"),
            )

    def test_local_spark_catalog_configuration(self):
        builder = _FakeBuilder()
        with patch.dict(sys.modules, _fake_pyspark(builder)):
            result = get_spark_session("/tmp/warehouse", "test-app")
        self.assertIs(result, builder)
        self.assertEqual(builder.values["spark.sql.catalog.local.type"], "hadoop")
        self.assertEqual(
            builder.values["spark.sql.catalog.local.warehouse"],
            str(Path("/tmp/warehouse").resolve()),
        )
        self.assertIn("iceberg-spark-runtime-3.5", builder.values["spark.jars.packages"])

    def test_rest_spark_catalog_configuration(self):
        builder = _FakeBuilder()
        environment = {
            "HARMO_ICEBERG_CATALOG_URI": "http://catalog:8181",
            "HARMO_S3_ENDPOINT": "http://object-store:9000",
            "HARMO_S3_PATH_STYLE_ACCESS": "true",
        }
        with patch.dict(os.environ, environment, clear=False), patch.dict(
            sys.modules, _fake_pyspark(builder)
        ):
            get_spark_session("s3://atlas/warehouse")
        self.assertEqual(builder.values["spark.sql.catalog.local.type"], "rest")
        self.assertEqual(
            builder.values["spark.sql.catalog.local.uri"], "http://catalog:8181"
        )
        self.assertEqual(
            builder.values["spark.sql.catalog.local.s3.endpoint"],
            "http://object-store:9000",
        )


if __name__ == "__main__":
    unittest.main()
