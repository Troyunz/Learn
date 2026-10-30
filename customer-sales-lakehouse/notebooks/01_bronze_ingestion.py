"""01 - Bronze ingestion: raw CSV -> bronze Parquet.

Reads every file in ``data/raw/`` with an explicit schema, stamps each row with audit
metadata, and writes the result to ``data/bronze/`` as Parquet.

Bronze is a faithful, un-opinionated landing zone:

* No casting beyond what the declared schema requires (everything stays as read).
* No trimming, no standardisation, no filtering, no de-duplication.
* Three audit columns are added: ``ingestion_timestamp``, ``source_file_name`` and
  ``processing_date``.
* A per-source-file audit (row count and ingestion time) is logged for each entity, so a
  partial or duplicated delivery is obvious immediately.

Run from the project root::

    python notebooks/01_bronze_ingestion.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python notebooks/01_bronze_ingestion.py` to import the `src` package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import StringType, StructField, StructType  # noqa: E402

from src import config  # noqa: E402
from src.ingestion import (  # noqa: E402
    add_audit_columns,
    get_spark_session,
    read_csv_to_df,
    write_parquet,
)
from src.logging_utils import get_logger, log_section  # noqa: E402

LOGGER = get_logger("01_bronze_ingestion")

# ---------------------------------------------------------------------------
# Explicit source schemas
#
# Every field is read as a string on purpose. Bronze must never lose data: if a source
# row contains "abc" in a numeric field, a typed read would turn it into NULL and the
# evidence would be gone. Casting happens in silver, where bad values can be flagged.
# ---------------------------------------------------------------------------

CUSTOMERS_SCHEMA = StructType(
    [
        StructField("customer_id", StringType(), nullable=True),
        StructField("customer_name", StringType(), nullable=True),
        StructField("region", StringType(), nullable=True),
        StructField("customer_created_date", StringType(), nullable=True),
    ]
)

PRODUCTS_SCHEMA = StructType(
    [
        StructField("product_id", StringType(), nullable=True),
        StructField("product_name", StringType(), nullable=True),
        StructField("category", StringType(), nullable=True),
        StructField("unit_price", StringType(), nullable=True),
    ]
)

ORDERS_SCHEMA = StructType(
    [
        StructField("order_id", StringType(), nullable=True),
        StructField("customer_id", StringType(), nullable=True),
        StructField("product_id", StringType(), nullable=True),
        StructField("order_date", StringType(), nullable=True),
        StructField("quantity", StringType(), nullable=True),
        StructField("amount", StringType(), nullable=True),
        StructField("status", StringType(), nullable=True),
        StructField("updated_at", StringType(), nullable=True),
    ]
)

#: (entity name, source CSV, target bronze folder, schema)
INGESTION_PLAN = [
    ("customers", config.RAW_CUSTOMERS, config.BRONZE_CUSTOMERS, CUSTOMERS_SCHEMA),
    ("products", config.RAW_PRODUCTS, config.BRONZE_PRODUCTS, PRODUCTS_SCHEMA),
    ("orders", config.RAW_ORDERS, config.BRONZE_ORDERS, ORDERS_SCHEMA),
]


def main() -> None:
    """Ingest all raw CSV files into the bronze layer."""
    log_section(LOGGER, "STAGE 01 | Bronze ingestion (raw -> bronze)")

    config.ensure_directories()
    spark = get_spark_session()
    spark.sparkContext.setLogLevel("WARN")

    LOGGER.info("Environment : %s", config.ENVIRONMENT)
    LOGGER.info("Data root   : %s", config.DATA_ROOT)

    try:
        total_rows = 0

        for entity, source_path, target_path, schema in INGESTION_PLAN:
            LOGGER.info("Reading %s from %s", entity, source_path)

            raw_df = read_csv_to_df(spark, source_path, schema)
            bronze_df = add_audit_columns(raw_df).cache()

            row_count = bronze_df.count()
            total_rows += row_count
            LOGGER.info("  %s: %s rows read", entity, row_count)

            # Audit the ingestion by source file: proves every expected file was picked
            # up, and makes a partial or duplicated delivery obvious straight away.
            file_audit = (
                bronze_df.groupBy("source_file_name")
                .agg(
                    F.count(F.lit(1)).alias("row_count"),
                    F.max("ingestion_timestamp").alias("ingested_at"),
                )
                .orderBy("source_file_name")
            )
            for audit_row in file_audit.collect():
                file_name = audit_row["source_file_name"].rsplit("/", 1)[-1]
                LOGGER.info(
                    "    source=%s rows=%s ingested_at=%s",
                    file_name,
                    audit_row["row_count"],
                    audit_row["ingested_at"],
                )

            write_parquet(bronze_df, target_path)
            LOGGER.info("  %s: written to %s", entity, target_path)

            bronze_df.unpersist()

        LOGGER.info(
            "Bronze ingestion complete: %s entities, %s rows total.",
            len(INGESTION_PLAN),
            total_rows,
        )
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
