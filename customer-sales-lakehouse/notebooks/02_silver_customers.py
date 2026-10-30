"""02 - Silver customers: bronze -> silver.customers.

Reads the bronze customers table and produces a cleaned, conformed customer master:

* Casts ``customer_id`` to ``INT`` and ``customer_created_date`` to ``DATE``.
* Trims and collapses whitespace in ``customer_name``.
* Standardises ``region`` to Title Case so casing variants conform.
* De-duplicates on ``customer_id``, keeping the most recently ingested row.
* Drops rows with a NULL ``customer_id`` - a customer without a key is unusable.

Output: ``data/silver/customers/`` (Parquet, overwrite).

Run from the project root::

    python notebooks/02_silver_customers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyspark.sql import DataFrame  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402

from src import config  # noqa: E402
from src.ingestion import (  # noqa: E402
    get_spark_session,
    read_parquet,
    write_parquet,
)
from src.logging_utils import get_logger, log_section  # noqa: E402
from src.transformations import (  # noqa: E402
    cast_columns,
    clean_string_columns,
    deduplicate_by_key,
    drop_rows_with_null_keys,
    parse_date_columns,
    standardise_title_case,
)

LOGGER = get_logger("02_silver_customers")

#: Final column order of the silver customers table.
SILVER_COLUMNS = [
    "customer_id",
    "customer_name",
    "region",
    "customer_created_date",
    "ingestion_timestamp",
    "processing_date",
]


def transform_customers(bronze_df: DataFrame) -> DataFrame:
    """Apply the full bronze -> silver customer transformation.

    Kept as a standalone function so the logic can be unit tested without touching disk.

    Args:
        bronze_df: Bronze customers, all business columns typed as strings.

    Returns:
        A cleaned, typed, de-duplicated customer DataFrame.
    """
    return (
        bronze_df.transform(lambda df: clean_string_columns(df, ["customer_name"]))
        .transform(lambda df: standardise_title_case(df, ["region"]))
        .transform(lambda df: cast_columns(df, {"customer_id": "int"}))
        .transform(lambda df: parse_date_columns(df, ["customer_created_date"]))
        .transform(lambda df: drop_rows_with_null_keys(df, ["customer_id"]))
        .transform(
            lambda df: deduplicate_by_key(df, ["customer_id"], ["ingestion_timestamp"])
        )
        .select(*SILVER_COLUMNS)
    )


def main() -> None:
    """Build the silver customers table."""
    log_section(LOGGER, "STAGE 02 | Silver customers (bronze -> silver)")

    config.ensure_directories()
    spark = get_spark_session()
    spark.sparkContext.setLogLevel("WARN")

    try:
        LOGGER.info("Reading bronze customers from %s", config.BRONZE_CUSTOMERS)
        bronze_df = read_parquet(spark, config.BRONZE_CUSTOMERS)
        bronze_count = bronze_df.count()

        silver_df = transform_customers(bronze_df)
        silver_count = silver_df.count()

        LOGGER.info("  bronze rows : %s", bronze_count)
        LOGGER.info("  silver rows : %s", silver_count)
        LOGGER.info("  rows removed: %s", bronze_count - silver_count)

        distinct_regions = [
            row["region"]
            for row in silver_df.select("region").distinct().orderBy("region").collect()
        ]
        LOGGER.info("  standardised regions: %s", distinct_regions)

        write_parquet(silver_df, config.SILVER_CUSTOMERS)
        LOGGER.info("Silver customers written to %s", config.SILVER_CUSTOMERS)

        silver_df.orderBy(F.col("customer_id")).show(truncate=False)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
