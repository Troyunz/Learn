"""04 - Silver orders: bronze -> silver.orders.

The most involved silver stage. It reads bronze orders and:

* Casts ``order_id``, ``customer_id``, ``product_id`` and ``quantity`` to ``INT``,
  ``amount`` to ``DECIMAL(10,2)``, ``order_date`` to ``DATE`` and ``updated_at`` to
  ``TIMESTAMP``.
* Standardises ``status`` to Title Case (``Completed`` / ``Cancelled`` / ``Pending``).
* **De-duplicates on ``order_id``**, keeping the row with the latest ``updated_at``.
  This resolves the duplicated ``order_id = 107`` and would also correctly apply a
  late-arriving correction, not just an exact duplicate.
* **Adds ``data_quality_status``** by left-joining to ``silver.customers``:

  1. ``Invalid Customer Reference`` - no matching customer.
  2. ``Missing Amount``            - ``amount IS NULL``.
  3. ``Valid``                     - passes every silver-level rule.

Bad rows are **flagged, not dropped**, so silver still reconciles with the source
system. Only ``Valid`` rows are later promoted into ``fact_sales``.

Depends on: stage 01 and stage 02.
Output: ``data/silver/orders/`` (Parquet, overwrite).

Run from the project root::

    python notebooks/04_silver_orders.py
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
    add_data_quality_status,
    cast_columns,
    deduplicate_by_key,
    drop_rows_with_null_keys,
    parse_date_columns,
    parse_timestamp_columns,
    standardise_title_case,
)

LOGGER = get_logger("04_silver_orders")

#: Target types for the orders business columns.
ORDER_TYPE_MAP = {
    "order_id": "int",
    "customer_id": "int",
    "product_id": "int",
    "quantity": "int",
    "amount": "decimal(10,2)",
}

#: Final column order of the silver orders table.
SILVER_COLUMNS = [
    "order_id",
    "customer_id",
    "product_id",
    "order_date",
    "quantity",
    "amount",
    "status",
    "updated_at",
    "data_quality_status",
    "ingestion_timestamp",
    "processing_date",
]


def transform_orders(bronze_orders: DataFrame, silver_customers: DataFrame) -> DataFrame:
    """Apply the full bronze -> silver order transformation.

    Args:
        bronze_orders: Bronze orders, all business columns typed as strings.
        silver_customers: Cleaned customers, used for the referential-integrity flag.

    Returns:
        A typed, standardised, de-duplicated and quality-flagged orders DataFrame.
    """
    cleaned = (
        bronze_orders.transform(lambda df: standardise_title_case(df, ["status"]))
        .transform(lambda df: cast_columns(df, ORDER_TYPE_MAP))
        .transform(lambda df: parse_date_columns(df, ["order_date"]))
        .transform(lambda df: parse_timestamp_columns(df, ["updated_at"]))
        .transform(lambda df: drop_rows_with_null_keys(df, ["order_id"]))
    )

    # Latest version of each order wins; ingestion_timestamp breaks any remaining tie.
    deduplicated = deduplicate_by_key(
        cleaned, ["order_id"], ["updated_at", "ingestion_timestamp"]
    )

    flagged = add_data_quality_status(deduplicated, silver_customers)

    return flagged.select(*SILVER_COLUMNS)


def main() -> None:
    """Build the silver orders table."""
    log_section(LOGGER, "STAGE 04 | Silver orders (bronze -> silver)")

    config.ensure_directories()
    spark = get_spark_session()
    spark.sparkContext.setLogLevel("WARN")

    try:
        LOGGER.info("Reading bronze orders from %s", config.BRONZE_ORDERS)
        bronze_orders = read_parquet(spark, config.BRONZE_ORDERS)

        LOGGER.info("Reading silver customers from %s", config.SILVER_CUSTOMERS)
        silver_customers = read_parquet(spark, config.SILVER_CUSTOMERS)

        bronze_count = bronze_orders.count()
        silver_df = transform_orders(bronze_orders, silver_customers).cache()
        silver_count = silver_df.count()

        LOGGER.info("  bronze rows       : %s", bronze_count)
        LOGGER.info("  silver rows       : %s", silver_count)
        LOGGER.info("  duplicates removed: %s", bronze_count - silver_count)

        LOGGER.info("Data-quality status breakdown:")
        for row in (
            silver_df.groupBy("data_quality_status")
            .count()
            .orderBy(F.col("count").desc())
            .collect()
        ):
            LOGGER.info("  %-28s %s", row["data_quality_status"], row["count"])

        LOGGER.info("Standardised status breakdown:")
        for row in silver_df.groupBy("status").count().orderBy("status").collect():
            LOGGER.info("  %-28s %s", row["status"], row["count"])

        write_parquet(silver_df, config.SILVER_ORDERS)
        LOGGER.info("Silver orders written to %s", config.SILVER_ORDERS)

        silver_df.orderBy(F.col("order_id")).show(truncate=False)
        silver_df.unpersist()
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
