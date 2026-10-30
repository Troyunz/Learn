"""Unit tests for the cleaning, de-duplication and quality-flag transformations.

Every test builds a tiny in-memory DataFrame, so the suite runs without any data on
disk and without the pipeline ever having been executed.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterator

import pytest
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.transformations import (  # noqa: E402
    STATUS_INVALID_CUSTOMER,
    STATUS_MISSING_AMOUNT,
    add_data_quality_status,
    add_surrogate_key,
    cast_columns,
    clean_string_columns,
    deduplicate_by_key,
    drop_rows_with_null_keys,
    parse_date_columns,
    parse_timestamp_columns,
    standardise_title_case,
    to_date_key,
)


@pytest.fixture(scope="session")
def spark() -> Iterator[SparkSession]:
    """Provide a single local SparkSession shared by the whole test session.

    Session scope matters: creating a SparkSession costs several seconds, so building
    one per test would dominate the runtime.

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


def _rows(df: DataFrame) -> list[dict]:
    """Collect a DataFrame into a list of plain dictionaries for easy assertions.

    Args:
        df: The DataFrame to materialise.

    Returns:
        One dictionary per row.
    """
    return [row.asDict() for row in df.collect()]


# ---------------------------------------------------------------------------
# clean_string_columns
# ---------------------------------------------------------------------------


def test_clean_string_columns_trims_and_collapses_whitespace(spark: SparkSession) -> None:
    """Leading/trailing and repeated internal whitespace is normalised."""
    df = spark.createDataFrame(
        [("  Aisha   Khan  ",), ("Ben\tSmith",)],
        ["customer_name"],
    )

    result = _rows(clean_string_columns(df, ["customer_name"]))

    assert [r["customer_name"] for r in result] == ["Aisha Khan", "Ben Smith"]


def test_clean_string_columns_converts_blanks_to_null(spark: SparkSession) -> None:
    """A field that is empty after trimming becomes NULL, not an empty string."""
    df = spark.createDataFrame([("   ",), ("",), ("Carla Jones",)], ["customer_name"])

    result = _rows(clean_string_columns(df, ["customer_name"]))

    assert [r["customer_name"] for r in result] == [None, None, "Carla Jones"]


# ---------------------------------------------------------------------------
# standardise_title_case
# ---------------------------------------------------------------------------


def test_standardise_title_case_conforms_casing_variants(spark: SparkSession) -> None:
    """Casing variants collapse into a single Title Case value."""
    df = spark.createDataFrame(
        [("north",), ("NORTH",), ("North",), ("  nOrTh ",)],
        ["region"],
    )

    result = _rows(standardise_title_case(df, ["region"]))

    assert {r["region"] for r in result} == {"North"}


def test_standardise_title_case_preserves_null(spark: SparkSession) -> None:
    """NULL input stays NULL rather than becoming an empty string."""
    df = spark.createDataFrame([(None,), ("south",)], "region string")

    result = _rows(standardise_title_case(df, ["region"]))

    assert [r["region"] for r in result] == [None, "South"]


# ---------------------------------------------------------------------------
# cast_columns / parse_date_columns / parse_timestamp_columns
# ---------------------------------------------------------------------------


def test_cast_columns_applies_target_types(spark: SparkSession) -> None:
    """String columns are cast to the requested numeric and decimal types."""
    df = spark.createDataFrame([("101", "200.00")], ["order_id", "amount"])

    casted = cast_columns(df, {"order_id": "int", "amount": "decimal(10,2)"})
    row = _rows(casted)[0]

    assert row["order_id"] == 101
    assert row["amount"] == Decimal("200.00")
    assert dict(casted.dtypes)["amount"] == "decimal(10,2)"


def test_cast_columns_turns_unparseable_values_into_null(spark: SparkSession) -> None:
    """A non-numeric value becomes NULL instead of failing the job."""
    df = spark.createDataFrame([("not-a-number",)], ["order_id"])

    row = _rows(cast_columns(df, {"order_id": "int"}))[0]

    assert row["order_id"] is None


def test_parse_date_and_timestamp_columns(spark: SparkSession) -> None:
    """Date and timestamp strings are parsed with explicit formats.

    The parsed timestamp is formatted back to a string *inside Spark* rather than
    collected as a ``datetime``. Collecting would convert the UTC session instant into
    the driver's local timezone, making the assertion depend on where the test runs.
    """
    df = spark.createDataFrame(
        [("2026-01-05", "2026-01-05 10:00:00")],
        ["order_date", "updated_at"],
    )

    parsed = parse_timestamp_columns(
        parse_date_columns(df, ["order_date"]), ["updated_at"]
    )
    row = _rows(
        parsed.select(
            F.col("order_date"),
            F.date_format("updated_at", "yyyy-MM-dd HH:mm:ss").alias("updated_at_str"),
        )
    )[0]

    assert row["order_date"] == date(2026, 1, 5)
    assert row["updated_at_str"] == "2026-01-05 10:00:00"
    assert dict(parsed.dtypes)["updated_at"] == "timestamp"


# ---------------------------------------------------------------------------
# drop_rows_with_null_keys
# ---------------------------------------------------------------------------


def test_drop_rows_with_null_keys_removes_keyless_rows(spark: SparkSession) -> None:
    """Rows without a business key are removed - they cannot be joined or flagged."""
    df = spark.createDataFrame([(1,), (None,), (3,)], "order_id int")

    result = _rows(drop_rows_with_null_keys(df, ["order_id"]))

    assert [r["order_id"] for r in result] == [1, 3]


# ---------------------------------------------------------------------------
# deduplicate_by_key
# ---------------------------------------------------------------------------


def test_deduplicate_by_key_removes_exact_duplicates(spark: SparkSession) -> None:
    """The duplicated order_id = 107 from the sample data collapses to one row."""
    df = spark.createDataFrame(
        [
            (107, datetime(2026, 2, 15, 8, 0, 0)),
            (107, datetime(2026, 2, 15, 8, 0, 0)),
            (101, datetime(2026, 1, 5, 10, 0, 0)),
        ],
        "order_id int, updated_at timestamp",
    )

    result = deduplicate_by_key(df, ["order_id"], ["updated_at"])

    assert result.count() == 2
    assert sorted(r["order_id"] for r in _rows(result)) == [101, 107]


def test_deduplicate_by_key_keeps_latest_version(spark: SparkSession) -> None:
    """A late-arriving correction wins over the earlier version of the same key."""
    df = spark.createDataFrame(
        [
            (107, "Pending", datetime(2026, 2, 15, 8, 0, 0)),
            (107, "Completed", datetime(2026, 2, 16, 9, 30, 0)),
        ],
        "order_id int, status string, updated_at timestamp",
    )

    result = _rows(deduplicate_by_key(df, ["order_id"], ["updated_at"]))

    assert len(result) == 1
    assert result[0]["status"] == "Completed"


def test_deduplicate_by_key_prefers_non_null_ordering_value(spark: SparkSession) -> None:
    """NULL ordering values sort last, so a real timestamp always wins."""
    df = spark.createDataFrame(
        [
            (107, "no-timestamp", None),
            (107, "has-timestamp", datetime(2026, 2, 15, 8, 0, 0)),
        ],
        "order_id int, label string, updated_at timestamp",
    )

    result = _rows(deduplicate_by_key(df, ["order_id"], ["updated_at"]))

    assert len(result) == 1
    assert result[0]["label"] == "has-timestamp"


def test_deduplicate_by_key_drops_helper_column(spark: SparkSession) -> None:
    """The internal _row_number window column must not leak into the output."""
    df = spark.createDataFrame([(1, datetime(2026, 1, 1))], "order_id int, updated_at timestamp")

    result = deduplicate_by_key(df, ["order_id"], ["updated_at"])

    assert "_row_number" not in result.columns


# ---------------------------------------------------------------------------
# add_data_quality_status
# ---------------------------------------------------------------------------


@pytest.fixture()
def customers(spark: SparkSession) -> DataFrame:
    """Provide a minimal customer lookup containing customer_ids 1, 2 and 3."""
    return spark.createDataFrame([(1,), (2,), (3,)], "customer_id int")


def test_add_data_quality_status_flags_valid_rows(
    spark: SparkSession, customers: DataFrame
) -> None:
    """An order with a known customer and a populated amount is Valid."""
    orders = spark.createDataFrame(
        [(101, 1, Decimal("200.00"))],
        "order_id int, customer_id int, amount decimal(10,2)",
    )

    row = _rows(add_data_quality_status(orders, customers))[0]

    assert row["data_quality_status"] == "Valid"


def test_add_data_quality_status_flags_invalid_customer_reference(
    spark: SparkSession, customers: DataFrame
) -> None:
    """Order 106 references customer 99, which does not exist."""
    orders = spark.createDataFrame(
        [(106, 99, Decimal("50.00"))],
        "order_id int, customer_id int, amount decimal(10,2)",
    )

    row = _rows(add_data_quality_status(orders, customers))[0]

    assert row["data_quality_status"] == STATUS_INVALID_CUSTOMER


def test_add_data_quality_status_flags_missing_amount(
    spark: SparkSession, customers: DataFrame
) -> None:
    """Order 105 has a known customer but no amount."""
    orders = spark.createDataFrame(
        [(105, 3, None)],
        "order_id int, customer_id int, amount decimal(10,2)",
    )

    row = _rows(add_data_quality_status(orders, customers))[0]

    assert row["data_quality_status"] == STATUS_MISSING_AMOUNT


def test_add_data_quality_status_prioritises_customer_reference(
    spark: SparkSession, customers: DataFrame
) -> None:
    """When both rules apply, the referential failure takes priority."""
    orders = spark.createDataFrame(
        [(999, 99, None)],
        "order_id int, customer_id int, amount decimal(10,2)",
    )

    row = _rows(add_data_quality_status(orders, customers))[0]

    assert row["data_quality_status"] == STATUS_INVALID_CUSTOMER


def test_add_data_quality_status_never_drops_or_duplicates_rows(
    spark: SparkSession, customers: DataFrame
) -> None:
    """Flagging must preserve the row count exactly - bad rows are kept, not dropped.

    Duplicate customer keys in the lookup must not fan the orders out either.
    """
    orders = spark.createDataFrame(
        [
            (101, 1, Decimal("200.00")),
            (105, 3, None),
            (106, 99, Decimal("50.00")),
        ],
        "order_id int, customer_id int, amount decimal(10,2)",
    )
    duplicated_customers = customers.unionByName(customers)

    result = add_data_quality_status(orders, duplicated_customers)

    assert result.count() == 3
    assert "_known_customer_id" not in result.columns


# ---------------------------------------------------------------------------
# Dimension helpers
# ---------------------------------------------------------------------------


def test_add_surrogate_key_creates_leading_key_column(spark: SparkSession) -> None:
    """The surrogate key is derived from the business key and placed first."""
    df = spark.createDataFrame([(1, "Aisha Khan")], "customer_id int, customer_name string")

    result = add_surrogate_key(df, "customer_id", "customer_key")
    row = _rows(result)[0]

    assert result.columns[0] == "customer_key"
    assert row["customer_key"] == row["customer_id"] == 1


def test_to_date_key_produces_yyyymmdd_integer(spark: SparkSession) -> None:
    """A DATE converts to an integer yyyyMMdd key suitable for dim_date joins."""
    df = spark.createDataFrame([(date(2026, 1, 5),)], "order_date date")

    row = _rows(df.select(to_date_key(df["order_date"]).alias("date_key")))[0]

    assert row["date_key"] == 20260105
