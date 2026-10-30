"""06 - Gold fact table: silver.orders + gold dimensions -> fact_sales.

Builds the additive sales fact at **order grain** (one row per order).

An order reaches ``fact_sales`` only if **both** conditions hold::

    data_quality_status = 'Valid'   AND   status = 'Completed'

With the bundled sample data that means:

====================  ==========================================================
Order                 Outcome
====================  ==========================================================
101, 102, 104, 107    Included - valid and completed.
103                   Excluded - ``Cancelled``, so not recognised revenue.
105                   Excluded - ``Pending`` and ``Missing Amount``.
106                   Excluded - ``Invalid Customer Reference`` (customer 99).
====================  ==========================================================

Dimension joins are **inner** joins on purpose: they act as a final referential guard,
so a fact row can never be left orphaned against a missing dimension member. The script
logs a warning if any rows are lost at that step.

Depends on: stages 04 and 05.
Output: ``data/gold/fact_sales/`` (Parquet, overwrite).

Run from the project root::

    python notebooks/06_gold_fact_sales.py
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
from src.transformations import to_date_key  # noqa: E402

LOGGER = get_logger("06_gold_fact_sales")

#: Final column order of the fact table.
FACT_COLUMNS = [
    "order_id",
    "customer_key",
    "product_key",
    "date_key",
    "quantity",
    "amount",
    "order_status",
]


def build_fact_sales(
    silver_orders: DataFrame,
    dim_customer: DataFrame,
    dim_product: DataFrame,
    dim_date: DataFrame,
) -> DataFrame:
    """Build the sales fact table from silver orders and the gold dimensions.

    Args:
        silver_orders: Quality-flagged silver orders.
        dim_customer: Customer dimension providing ``customer_key``.
        dim_product: Product dimension providing ``product_key``.
        dim_date: Date dimension providing ``date_key``.

    Returns:
        ``fact_sales`` at order grain, containing only valid completed orders.
    """
    eligible = silver_orders.filter(
        (F.col("data_quality_status") == F.lit(config.VALID_QUALITY_STATUS))
        & (F.col("status") == F.lit(config.REVENUE_STATUS))
    ).withColumn("date_key", to_date_key(F.col("order_date")))

    return (
        eligible.join(
            dim_customer.select("customer_key", "customer_id"), on="customer_id", how="inner"
        )
        .join(
            dim_product.select("product_key", "product_id"), on="product_id", how="inner"
        )
        .join(dim_date.select("date_key"), on="date_key", how="inner")
        .select(
            F.col("order_id"),
            F.col("customer_key"),
            F.col("product_key"),
            F.col("date_key"),
            F.col("quantity"),
            F.col("amount"),
            F.col("status").alias("order_status"),
        )
        .select(*FACT_COLUMNS)
    )


def main() -> None:
    """Build and persist the gold fact_sales table."""
    log_section(LOGGER, "STAGE 06 | Gold fact_sales (silver + dims -> gold)")

    config.ensure_directories()
    spark = get_spark_session()
    spark.sparkContext.setLogLevel("WARN")

    try:
        silver_orders = read_parquet(spark, config.SILVER_ORDERS).cache()
        dim_customer = read_parquet(spark, config.GOLD_DIM_CUSTOMER)
        dim_product = read_parquet(spark, config.GOLD_DIM_PRODUCT)
        dim_date = read_parquet(spark, config.GOLD_DIM_DATE)

        total_orders = silver_orders.count()
        eligible_orders = silver_orders.filter(
            (F.col("data_quality_status") == F.lit(config.VALID_QUALITY_STATUS))
            & (F.col("status") == F.lit(config.REVENUE_STATUS))
        ).count()

        fact_sales = build_fact_sales(
            silver_orders, dim_customer, dim_product, dim_date
        ).cache()
        fact_count = fact_sales.count()

        LOGGER.info("  silver orders      : %s", total_orders)
        LOGGER.info("  eligible for fact  : %s", eligible_orders)
        LOGGER.info("  excluded by filters: %s", total_orders - eligible_orders)
        LOGGER.info("  fact_sales rows    : %s", fact_count)

        if fact_count != eligible_orders:
            LOGGER.warning(
                "  %s eligible order(s) dropped by a dimension join - "
                "check for missing dimension members.",
                eligible_orders - fact_count,
            )

        total_revenue = fact_sales.select(F.sum("amount").alias("total")).collect()[0][
            "total"
        ]
        LOGGER.info("  recognised revenue : %s", total_revenue)

        write_parquet(fact_sales, config.GOLD_FACT_SALES)
        LOGGER.info("fact_sales written to %s", config.GOLD_FACT_SALES)

        fact_sales.orderBy("order_id").show(truncate=False)

        fact_sales.unpersist()
        silver_orders.unpersist()
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
