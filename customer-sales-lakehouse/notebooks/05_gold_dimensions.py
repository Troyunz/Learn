"""05 - Gold dimensions: silver -> dim_customer, dim_product, dim_date.

Builds the three conformed dimensions of the star schema:

* ``dim_customer`` - one row per customer, keyed by ``customer_key``.
* ``dim_product``  - one row per product, keyed by ``product_key``.
* ``dim_date``     - a contiguous calendar generated from the min/max ``order_date``
  found in ``silver.orders``, keyed by an integer ``date_key`` (``yyyyMMdd``).

``dim_date`` is generated with ``sequence()`` rather than being derived from the
distinct order dates, so the calendar has **no gaps**. That matters for reporting:
a month with zero sales must still appear in a time series.

Surrogate keys are derived 1:1 from the business keys to keep this example readable; a
production build would use an independent sequence so the key survives source-system
identifier changes.

Depends on: stages 02, 03 and 04.
Output: ``data/gold/dim_customer/``, ``dim_product/``, ``dim_date/``.

Run from the project root::

    python notebooks/05_gold_dimensions.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyspark.sql import DataFrame, SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402

from src import config  # noqa: E402
from src.ingestion import (  # noqa: E402
    get_spark_session,
    read_parquet,
    write_parquet,
)
from src.logging_utils import get_logger, log_section  # noqa: E402
from src.transformations import add_surrogate_key  # noqa: E402

LOGGER = get_logger("05_gold_dimensions")

#: Padding added around the observed order-date range so the calendar covers full years.
CALENDAR_PADDING_DAYS = 0


def build_dim_customer(silver_customers: DataFrame) -> DataFrame:
    """Build the customer dimension.

    Args:
        silver_customers: Cleaned silver customers.

    Returns:
        ``dim_customer`` with a ``customer_key`` surrogate key.
    """
    return add_surrogate_key(
        silver_customers.select(
            "customer_id", "customer_name", "region", "customer_created_date"
        ).dropDuplicates(["customer_id"]),
        source_column="customer_id",
        key_column="customer_key",
    )


def build_dim_product(silver_products: DataFrame) -> DataFrame:
    """Build the product dimension.

    Args:
        silver_products: Cleaned silver products.

    Returns:
        ``dim_product`` with a ``product_key`` surrogate key.
    """
    return add_surrogate_key(
        silver_products.select(
            "product_id", "product_name", "category", "unit_price"
        ).dropDuplicates(["product_id"]),
        source_column="product_id",
        key_column="product_key",
    )


def build_dim_date(spark: SparkSession, silver_orders: DataFrame) -> DataFrame:
    """Build a gap-free date dimension covering every observed order date.

    Args:
        spark: Active SparkSession, used to create the single-row seed DataFrame.
        silver_orders: Silver orders, used only to derive the calendar boundaries.

    Returns:
        ``dim_date`` with one row per calendar day in the covered range.
    """
    bounds = silver_orders.select(
        F.min("order_date").alias("min_date"),
        F.max("order_date").alias("max_date"),
    ).collect()[0]

    min_date, max_date = bounds["min_date"], bounds["max_date"]
    LOGGER.info("  calendar range: %s -> %s", min_date, max_date)

    # explode(sequence(...)) materialises one row per day with no gaps.
    calendar = (
        spark.createDataFrame([(min_date, max_date)], ["start_date", "end_date"])
        .select(
            F.explode(
                F.sequence(
                    F.date_sub(F.col("start_date"), CALENDAR_PADDING_DAYS),
                    F.date_add(F.col("end_date"), CALENDAR_PADDING_DAYS),
                    F.expr("INTERVAL 1 DAY"),
                )
            ).alias("full_date")
        )
    )

    return calendar.select(
        F.date_format("full_date", "yyyyMMdd").cast("int").alias("date_key"),
        F.col("full_date"),
        F.year("full_date").alias("year"),
        F.quarter("full_date").alias("quarter"),
        F.month("full_date").alias("month"),
        F.date_format("full_date", "MMMM").alias("month_name"),
        F.dayofmonth("full_date").alias("day"),
        F.dayofweek("full_date").alias("day_of_week"),
        F.date_format("full_date", "EEEE").alias("day_name"),
        # dayofweek(): 1 = Sunday ... 7 = Saturday
        F.dayofweek("full_date").isin(1, 7).alias("is_weekend"),
    ).orderBy("date_key")


def main() -> None:
    """Build and persist all gold dimension tables."""
    log_section(LOGGER, "STAGE 05 | Gold dimensions (silver -> gold)")

    config.ensure_directories()
    spark = get_spark_session()
    spark.sparkContext.setLogLevel("WARN")

    try:
        silver_customers = read_parquet(spark, config.SILVER_CUSTOMERS)
        silver_products = read_parquet(spark, config.SILVER_PRODUCTS)
        silver_orders = read_parquet(spark, config.SILVER_ORDERS)

        LOGGER.info("Building dim_customer ...")
        dim_customer = build_dim_customer(silver_customers)
        write_parquet(dim_customer, config.GOLD_DIM_CUSTOMER)
        LOGGER.info("  dim_customer rows: %s", dim_customer.count())

        LOGGER.info("Building dim_product ...")
        dim_product = build_dim_product(silver_products)
        write_parquet(dim_product, config.GOLD_DIM_PRODUCT)
        LOGGER.info("  dim_product rows: %s", dim_product.count())

        LOGGER.info("Building dim_date ...")
        dim_date = build_dim_date(spark, silver_orders)
        write_parquet(dim_date, config.GOLD_DIM_DATE)
        LOGGER.info("  dim_date rows: %s", dim_date.count())

        LOGGER.info("Gold dimensions written to %s", config.GOLD_DIR)

        dim_customer.orderBy("customer_key").show(truncate=False)
        dim_product.orderBy("product_key").show(truncate=False)
        dim_date.show(5, truncate=False)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
