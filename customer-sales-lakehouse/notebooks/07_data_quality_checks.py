"""07 - Data-quality checks: silver -> quality_results.

Runs the four standard checks defined in ``src/quality_checks.py`` against the silver
layer and persists the outcome to ``data/quality_results/``:

* ``duplicate_order_id``         - ``order_id`` must be unique.
* ``missing_order_amount``       - ``amount`` must not be NULL.
* ``invalid_customer_reference`` - every ``customer_id`` must exist in customers.
* ``invalid_status``             - ``status`` must be Completed / Cancelled / Pending.

Expected outcome with the bundled sample data::

    duplicate_order_id           PASS   0   <- fixed by stage 04 de-duplication
    missing_order_amount         FAIL   1   <- order 105, intentional
    invalid_customer_reference   FAIL   1   <- order 106 -> customer 99, intentional
    invalid_status               PASS   0   <- fixed by stage 04 standardisation

The two failures are **intentional**: they prove the framework detects the defects
planted in ``data/raw/orders.csv``. The job therefore **records** failures rather than
aborting, so the results can be trended over time. Set ``FAIL_ON_ERROR = True`` to turn
the checks into a hard gate instead.

Depends on: stages 02 and 04.
Output: ``data/quality_results/`` (Parquet, overwrite).

Run from the project root::

    python notebooks/07_data_quality_checks.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pyspark.sql import DataFrame, SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import (  # noqa: E402
    LongType,
    StringType,
    StructField,
    StructType,
)

from src import config  # noqa: E402
from src.ingestion import (  # noqa: E402
    get_spark_session,
    read_parquet,
    write_parquet,
)
from src.logging_utils import get_logger, log_section  # noqa: E402
from src.quality_checks import (  # noqa: E402
    ALL_CHECKS,
    FAIL,
    CheckResult,
    run_all_checks,
    summarise,
)

LOGGER = get_logger("07_data_quality_checks")

#: When True, a failing check raises and stops the pipeline. Kept False so that the
#: intentionally-planted defects in the sample data do not break a learning run.
FAIL_ON_ERROR: bool = False

#: Schema of the persisted results table.
RESULTS_SCHEMA = StructType(
    [
        StructField("check_name", StringType(), nullable=False),
        StructField("status", StringType(), nullable=False),
        StructField("failed_count", LongType(), nullable=False),
    ]
)


def results_to_dataframe(
    spark: SparkSession, results: list[CheckResult]
) -> DataFrame:
    """Convert check results into a DataFrame ready to persist.

    Args:
        spark: Active SparkSession.
        results: Results returned by :func:`src.quality_checks.run_all_checks`.

    Returns:
        A DataFrame matching :data:`RESULTS_SCHEMA` plus a ``run_timestamp`` column.
    """
    rows = [(r.check_name, r.status, int(r.failed_count)) for r in results]
    return spark.createDataFrame(rows, schema=RESULTS_SCHEMA).withColumn(
        "run_timestamp", F.current_timestamp()
    )


def main() -> None:
    """Run every quality check and persist the results."""
    log_section(LOGGER, "STAGE 07 | Data-quality checks (silver -> quality_results)")

    config.ensure_directories()
    spark = get_spark_session()
    spark.sparkContext.setLogLevel("WARN")

    try:
        LOGGER.info("Reading silver orders from %s", config.SILVER_ORDERS)
        silver_orders = read_parquet(spark, config.SILVER_ORDERS).cache()

        LOGGER.info("Reading silver customers from %s", config.SILVER_CUSTOMERS)
        silver_customers = read_parquet(spark, config.SILVER_CUSTOMERS).cache()

        LOGGER.info("Running %s checks ...", len(ALL_CHECKS))
        results = run_all_checks(silver_orders, silver_customers)

        for line in summarise(results).splitlines():
            LOGGER.info(line)

        results_df = results_to_dataframe(spark, results)
        write_parquet(results_df, config.QUALITY_RESULTS_DIR)
        LOGGER.info("Results written to %s", config.QUALITY_RESULTS_DIR)

        failed = [r for r in results if r.status == FAIL]
        if failed:
            LOGGER.warning(
                "%s of %s checks failed: %s",
                len(failed),
                len(results),
                ", ".join(r.check_name for r in failed),
            )
            if FAIL_ON_ERROR:
                raise RuntimeError(
                    f"Data-quality gate failed: {[r.check_name for r in failed]}"
                )
        else:
            LOGGER.info("All %s checks passed.", len(results))

        silver_orders.unpersist()
        silver_customers.unpersist()
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
