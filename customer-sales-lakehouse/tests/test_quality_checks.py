"""Unit tests for the four data-quality checks.

The ``bad_orders`` fixture reproduces the exact defects planted in
``data/raw/orders.csv`` so the tests assert against known-bad data:

* ``order_id = 107`` is duplicated.
* ``order_id = 105`` has a NULL ``amount``.
* ``order_id = 106`` references ``customer_id = 99``, which does not exist.
* an extra row with ``status = 'Shipped'`` exercises the status-domain check.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterator

import pytest
from pyspark.sql import DataFrame, SparkSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.quality_checks import (  # noqa: E402
    ALL_CHECKS,
    FAIL,
    PASS,
    CheckResult,
    check_duplicate_order_id,
    check_invalid_customer_reference,
    check_invalid_status,
    check_missing_order_amount,
    run_all_checks,
    summarise,
)

ORDERS_SCHEMA = (
    "order_id int, customer_id int, product_id int, order_date date, "
    "quantity int, amount decimal(10,2), status string"
)

CUSTOMERS_SCHEMA = "customer_id int, customer_name string, region string"


@pytest.fixture(scope="session")
def spark() -> Iterator[SparkSession]:
    """Provide a single local SparkSession shared by the whole test session.

    Yields:
        An active local :class:`~pyspark.sql.SparkSession`.
    """
    session = (
        SparkSession.builder.appName("customer-sales-lakehouse-tests")
        .master("local[1]")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture()
def good_customers(spark: SparkSession) -> DataFrame:
    """Provide the clean customer master (ids 1-4), mirroring customers.csv."""
    return spark.createDataFrame(
        [
            (1, "Aisha Khan", "North"),
            (2, "Ben Smith", "South"),
            (3, "Carla Jones", "North"),
            (4, "David Lee", "West"),
        ],
        CUSTOMERS_SCHEMA,
    )


@pytest.fixture()
def bad_orders(spark: SparkSession) -> DataFrame:
    """Provide orders containing one instance of every defect under test."""
    return spark.createDataFrame(
        [
            (101, 1, 1003, date(2026, 1, 5), 1, Decimal("200.00"), "Completed"),
            (105, 3, 1002, date(2026, 2, 5), 1, None, "Pending"),          # NULL amount
            (106, 99, 1001, date(2026, 2, 10), 1, Decimal("50.00"), "Completed"),  # orphan
            (107, 1, 1003, date(2026, 2, 15), 1, Decimal("200.00"), "Completed"),
            (107, 1, 1003, date(2026, 2, 15), 1, Decimal("200.00"), "Completed"),  # dup
            (108, 2, 1001, date(2026, 2, 20), 1, Decimal("50.00"), "Shipped"),  # bad status
        ],
        ORDERS_SCHEMA,
    )


@pytest.fixture()
def clean_orders(spark: SparkSession) -> DataFrame:
    """Provide orders that violate none of the four rules."""
    return spark.createDataFrame(
        [
            (101, 1, 1003, date(2026, 1, 5), 1, Decimal("200.00"), "Completed"),
            (102, 1, 1002, date(2026, 1, 10), 2, Decimal("50.00"), "Completed"),
            (103, 2, 1001, date(2026, 1, 12), 1, Decimal("50.00"), "Cancelled"),
            (104, 3, 1004, date(2026, 2, 1), 1, Decimal("75.00"), "Pending"),
        ],
        ORDERS_SCHEMA,
    )


# ---------------------------------------------------------------------------
# check_duplicate_order_id
# ---------------------------------------------------------------------------


def test_duplicate_order_id_detects_duplicate_key(bad_orders: DataFrame) -> None:
    """order_id 107 appears twice, so exactly one key fails."""
    result = check_duplicate_order_id(bad_orders)

    assert result.check_name == "duplicate_order_id"
    assert result.status == FAIL
    assert result.failed_count == 1


def test_duplicate_order_id_passes_on_unique_keys(clean_orders: DataFrame) -> None:
    """A table with unique keys passes with a zero failure count."""
    result = check_duplicate_order_id(clean_orders)

    assert result.status == PASS
    assert result.failed_count == 0


def test_duplicate_order_id_counts_keys_not_excess_rows(spark: SparkSession) -> None:
    """A key appearing three times still contributes only 1 to the failure count."""
    df = spark.createDataFrame([(107,), (107,), (107,), (101,)], "order_id int")

    assert check_duplicate_order_id(df).failed_count == 1


# ---------------------------------------------------------------------------
# check_missing_order_amount
# ---------------------------------------------------------------------------


def test_missing_order_amount_detects_null(bad_orders: DataFrame) -> None:
    """order_id 105 has a NULL amount."""
    result = check_missing_order_amount(bad_orders)

    assert result.check_name == "missing_order_amount"
    assert result.status == FAIL
    assert result.failed_count == 1


def test_missing_order_amount_passes_when_fully_populated(clean_orders: DataFrame) -> None:
    """No NULL amounts means the check passes."""
    result = check_missing_order_amount(clean_orders)

    assert result.status == PASS
    assert result.failed_count == 0


def test_missing_order_amount_does_not_flag_zero(spark: SparkSession) -> None:
    """A zero amount is a real value, not a missing one."""
    df = spark.createDataFrame([(1, Decimal("0.00"))], "order_id int, amount decimal(10,2)")

    assert check_missing_order_amount(df).failed_count == 0


# ---------------------------------------------------------------------------
# check_invalid_customer_reference
# ---------------------------------------------------------------------------


def test_invalid_customer_reference_detects_orphan(
    bad_orders: DataFrame, good_customers: DataFrame
) -> None:
    """order_id 106 points at customer 99, which is not in the customer master."""
    result = check_invalid_customer_reference(bad_orders, good_customers)

    assert result.check_name == "invalid_customer_reference"
    assert result.status == FAIL
    assert result.failed_count == 1


def test_invalid_customer_reference_passes_when_all_resolve(
    clean_orders: DataFrame, good_customers: DataFrame
) -> None:
    """Every customer_id resolves, so the check passes."""
    result = check_invalid_customer_reference(clean_orders, good_customers)

    assert result.status == PASS
    assert result.failed_count == 0


def test_invalid_customer_reference_is_not_inflated_by_duplicate_customers(
    bad_orders: DataFrame, good_customers: DataFrame
) -> None:
    """Duplicate rows in the lookup must not fan out the anti-join count."""
    duplicated = good_customers.unionByName(good_customers)

    result = check_invalid_customer_reference(bad_orders, duplicated)

    assert result.failed_count == 1


def test_invalid_customer_reference_flags_null_foreign_key(
    spark: SparkSession, good_customers: DataFrame
) -> None:
    """A NULL customer_id cannot resolve to a customer and must be flagged."""
    df = spark.createDataFrame([(1, None)], "order_id int, customer_id int")

    assert check_invalid_customer_reference(df, good_customers).failed_count == 1


# ---------------------------------------------------------------------------
# check_invalid_status
# ---------------------------------------------------------------------------


def test_invalid_status_detects_out_of_domain_value(bad_orders: DataFrame) -> None:
    """'Shipped' is outside the allowed domain."""
    result = check_invalid_status(bad_orders)

    assert result.check_name == "invalid_status"
    assert result.status == FAIL
    assert result.failed_count == 1


def test_invalid_status_passes_on_allowed_domain(clean_orders: DataFrame) -> None:
    """Completed / Cancelled / Pending are all allowed."""
    result = check_invalid_status(clean_orders)

    assert result.status == PASS
    assert result.failed_count == 0


def test_invalid_status_flags_null_status(spark: SparkSession) -> None:
    """A NULL status is unknown, so it fails the domain check."""
    df = spark.createDataFrame([(1, None)], "order_id int, status string")

    assert check_invalid_status(df).failed_count == 1


def test_invalid_status_is_case_sensitive(spark: SparkSession) -> None:
    """Un-standardised casing fails, which is why stage 04 applies initcap first."""
    df = spark.createDataFrame([(1, "completed")], "order_id int, status string")

    assert check_invalid_status(df).failed_count == 1


# ---------------------------------------------------------------------------
# run_all_checks / reporting helpers
# ---------------------------------------------------------------------------


def test_run_all_checks_returns_every_registered_check(
    bad_orders: DataFrame, good_customers: DataFrame
) -> None:
    """The suite returns one result per registered check, in the declared order."""
    results = run_all_checks(bad_orders, good_customers)

    assert [r.check_name for r in results] == list(ALL_CHECKS)


def test_run_all_checks_on_known_bad_data(
    bad_orders: DataFrame, good_customers: DataFrame
) -> None:
    """Every planted defect is detected exactly once."""
    results = {r.check_name: r for r in run_all_checks(bad_orders, good_customers)}

    assert results["duplicate_order_id"].failed_count == 1
    assert results["missing_order_amount"].failed_count == 1
    assert results["invalid_customer_reference"].failed_count == 1
    assert results["invalid_status"].failed_count == 1
    assert all(r.status == FAIL for r in results.values())


def test_run_all_checks_on_clean_data(
    clean_orders: DataFrame, good_customers: DataFrame
) -> None:
    """Clean data produces four passing checks with zero failures."""
    results = run_all_checks(clean_orders, good_customers)

    assert all(r.status == PASS for r in results)
    assert sum(r.failed_count for r in results) == 0


def test_check_result_unpacks_as_a_tuple(clean_orders: DataFrame) -> None:
    """The (check_name, status, failed_count) contract holds for tuple unpacking."""
    check_name, status, failed_count = check_missing_order_amount(clean_orders)

    assert isinstance(check_name, str)
    assert status in (PASS, FAIL)
    assert failed_count == 0


def test_summarise_renders_every_check(
    bad_orders: DataFrame, good_customers: DataFrame
) -> None:
    """The console summary contains a line for each check."""
    output = summarise(run_all_checks(bad_orders, good_customers))

    for name in ALL_CHECKS:
        assert name in output


def test_check_result_is_a_named_tuple() -> None:
    """Attribute access and tuple semantics both work on CheckResult."""
    result = CheckResult(check_name="demo", status=PASS, failed_count=0)

    assert result.check_name == "demo"
    assert tuple(result) == ("demo", PASS, 0)
