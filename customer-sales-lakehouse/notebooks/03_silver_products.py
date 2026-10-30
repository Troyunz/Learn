"""03 - Silver products: bronze -> silver.products.

Reads the bronze products table and produces a cleaned, conformed product master:

* Casts ``product_id`` to ``INT`` and ``unit_price`` to ``DECIMAL(10,2)``.
* Trims and collapses whitespace in ``product_name``.
* Standardises ``category`` to Title Case.
* De-duplicates on ``product_id``, keeping the most recently ingested row.
* Drops rows with a NULL ``product_id``.

``DECIMAL(10,2)`` is used rather than ``DOUBLE`` because money must not be subject to
binary floating-point rounding error.

Output: ``data/silver/products/`` (Parquet, overwrite).

Run from the project root::

    python notebooks/03_silver_products.py
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
    standardise_title_case,
)

LOGGER = get_logger("03_silver_products")

#: Final column order of the silver products table.
SILVER_COLUMNS = [
    "product_id",
    "product_name",
    "category",
    "unit_price",
    "ingestion_timestamp",
    "processing_date",
]


def transform_products(bronze_df: DataFrame) -> DataFrame:
    """Apply the full bronze -> silver product transformation.

    Args:
        bronze_df: Bronze products, all business columns typed as strings.

    Returns:
        A cleaned, typed, de-duplicated product DataFrame.
    """
    return (
        bronze_df.transform(lambda df: clean_string_columns(df, ["product_name"]))
        .transform(lambda df: standardise_title_case(df, ["category"]))
        .transform(
            lambda df: cast_columns(
                df, {"product_id": "int", "unit_price": "decimal(10,2)"}
            )
        )
        .transform(lambda df: drop_rows_with_null_keys(df, ["product_id"]))
        .transform(
            lambda df: deduplicate_by_key(df, ["product_id"], ["ingestion_timestamp"])
        )
        .select(*SILVER_COLUMNS)
    )


def main() -> None:
    """Build the silver products table."""
    log_section(LOGGER, "STAGE 03 | Silver products (bronze -> silver)")

    config.ensure_directories()
    spark = get_spark_session()
    spark.sparkContext.setLogLevel("WARN")

    try:
        LOGGER.info("Reading bronze products from %s", config.BRONZE_PRODUCTS)
        bronze_df = read_parquet(spark, config.BRONZE_PRODUCTS)
        bronze_count = bronze_df.count()

        silver_df = transform_products(bronze_df)
        silver_count = silver_df.count()

        LOGGER.info("  bronze rows : %s", bronze_count)
        LOGGER.info("  silver rows : %s", silver_count)
        LOGGER.info("  rows removed: %s", bronze_count - silver_count)

        distinct_categories = [
            row["category"]
            for row in silver_df.select("category")
            .distinct()
            .orderBy("category")
            .collect()
        ]
        LOGGER.info("  standardised categories: %s", distinct_categories)

        write_parquet(silver_df, config.SILVER_PRODUCTS)
        LOGGER.info("Silver products written to %s", config.SILVER_PRODUCTS)

        silver_df.orderBy(F.col("product_id")).show(truncate=False)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
