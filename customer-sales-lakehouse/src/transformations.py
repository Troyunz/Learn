"""Reusable cleaning, de-duplication and quality-flagging transformations.

Every function takes a DataFrame and returns a new DataFrame, so they compose cleanly
with :meth:`pyspark.sql.DataFrame.transform` and are trivial to unit test against small
in-memory DataFrames (see ``tests/test_transformations.py``).

The rules implemented here are documented in ``docs/transformation_rules.md``.
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from src.config import VALID_QUALITY_STATUS

#: ``data_quality_status`` emitted when an order references a non-existent customer.
STATUS_INVALID_CUSTOMER: str = "Invalid Customer Reference"

#: ``data_quality_status`` emitted when an order has no monetary amount.
STATUS_MISSING_AMOUNT: str = "Missing Amount"


def _clean_text(column: Column) -> Column:
    """Trim, collapse internal whitespace, and convert blanks to NULL.

    Args:
        column: The string column expression to clean.

    Returns:
        A cleaned column expression.
    """
    collapsed = F.trim(F.regexp_replace(column, r"\s+", " "))
    # A field that is empty after trimming carries no information - treat it as missing.
    return F.when(collapsed == "", F.lit(None).cast("string")).otherwise(collapsed)


def clean_string_columns(df: DataFrame, columns: list[str]) -> DataFrame:
    """Apply standard text cleaning to the given string columns.

    Args:
        df: Source DataFrame.
        columns: Names of the string columns to clean.

    Returns:
        A DataFrame with each named column trimmed, whitespace-collapsed, and with
        blank values converted to ``NULL``.

    Example:
        >>> clean_string_columns(df, ["customer_name"])
    """
    for column_name in columns:
        df = df.withColumn(column_name, _clean_text(F.col(column_name)))
    return df


def standardise_title_case(df: DataFrame, columns: list[str]) -> DataFrame:
    """Normalise categorical text columns to Title Case.

    This conforms casing variants such as ``"north"``, ``"NORTH"`` and ``"North"`` to a
    single value so that ``GROUP BY region`` produces one bucket rather than three.

    Args:
        df: Source DataFrame.
        columns: Names of the categorical columns to standardise.

    Returns:
        A DataFrame with each named column cleaned and converted to Title Case.
    """
    for column_name in columns:
        df = df.withColumn(column_name, F.initcap(_clean_text(F.col(column_name))))
    return df


def cast_columns(df: DataFrame, type_map: dict[str, str]) -> DataFrame:
    """Cast columns to explicit target types.

    Values that cannot be parsed become ``NULL`` rather than failing the job; the
    quality framework is responsible for reporting them.

    Args:
        df: Source DataFrame.
        type_map: Mapping of column name to Spark SQL type string, for example
            ``{"customer_id": "int", "unit_price": "decimal(10,2)"}``.

    Returns:
        A DataFrame with the requested columns cast.
    """
    for column_name, target_type in type_map.items():
        df = df.withColumn(column_name, F.col(column_name).cast(target_type))
    return df


def parse_date_columns(df: DataFrame, columns: list[str], fmt: str = "yyyy-MM-dd") -> DataFrame:
    """Parse string columns into ``DATE`` values using an explicit format.

    Args:
        df: Source DataFrame.
        columns: Names of the columns to parse.
        fmt: Java/Spark date pattern describing the source format.

    Returns:
        A DataFrame with the named columns typed as ``DATE``.
    """
    for column_name in columns:
        df = df.withColumn(column_name, F.to_date(F.col(column_name), fmt))
    return df


def parse_timestamp_columns(
    df: DataFrame, columns: list[str], fmt: str = "yyyy-MM-dd HH:mm:ss"
) -> DataFrame:
    """Parse string columns into ``TIMESTAMP`` values using an explicit format.

    Args:
        df: Source DataFrame.
        columns: Names of the columns to parse.
        fmt: Java/Spark timestamp pattern describing the source format.

    Returns:
        A DataFrame with the named columns typed as ``TIMESTAMP``.
    """
    for column_name in columns:
        df = df.withColumn(column_name, F.to_timestamp(F.col(column_name), fmt))
    return df


def drop_rows_with_null_keys(df: DataFrame, key_columns: list[str]) -> DataFrame:
    """Remove rows whose business key is missing.

    A row without a key cannot be joined, de-duplicated or referenced, so it is the one
    category of record that is dropped rather than flagged.

    Args:
        df: Source DataFrame.
        key_columns: Columns that together form the business key.

    Returns:
        A DataFrame containing only rows with a fully populated key.
    """
    return df.dropna(subset=key_columns)


def deduplicate_by_key(
    df: DataFrame,
    key_columns: list[str],
    order_by_columns: list[str] | None = None,
) -> DataFrame:
    """Keep exactly one row per business key, preferring the most recent version.

    Uses ``row_number()`` over a window partitioned by the key and ordered descending by
    ``order_by_columns``. Unlike ``dropDuplicates``, this is deterministic and correctly
    handles *late-arriving corrections* - two rows with the same key but different
    content - not just byte-identical duplicates.

    ``NULL`` ordering values sort last, so a row with a real timestamp always wins over
    a row without one.

    Args:
        df: Source DataFrame.
        key_columns: Columns that form the business key, for example ``["order_id"]``.
        order_by_columns: Recency columns, most significant first, for example
            ``["updated_at", "ingestion_timestamp"]``. When omitted, an arbitrary but
            stable row is kept.

    Returns:
        A DataFrame with one row per key and no helper columns left behind.

    Example:
        >>> deduplicate_by_key(orders, ["order_id"], ["updated_at"])
    """
    if order_by_columns:
        ordering = [F.col(c).desc_nulls_last() for c in order_by_columns]
    else:
        ordering = [F.lit(1)]

    window = Window.partitionBy(*key_columns).orderBy(*ordering)

    return (
        df.withColumn("_row_number", F.row_number().over(window))
        .filter(F.col("_row_number") == 1)
        .drop("_row_number")
    )


def add_data_quality_status(
    orders_df: DataFrame,
    customers_df: DataFrame,
    customer_id_column: str = "customer_id",
    amount_column: str = "amount",
) -> DataFrame:
    """Flag each order with a ``data_quality_status`` instead of deleting bad rows.

    Rules are applied in priority order:

    1. ``Invalid Customer Reference`` - no matching customer exists.
    2. ``Missing Amount`` - the order has no monetary amount.
    3. ``Valid`` - the order passes every silver-level check.

    Keeping flagged rows means silver still reconciles with the source system, while the
    gold fact table can filter on ``Valid`` to stay trustworthy.

    Args:
        orders_df: Cleaned, de-duplicated orders.
        customers_df: Cleaned customers, used as the referential-integrity lookup.
        customer_id_column: Name of the foreign-key column on ``orders_df``.
        amount_column: Name of the monetary column to null-check.

    Returns:
        ``orders_df`` plus a non-null ``data_quality_status`` string column. No order
        rows are added or removed.
    """
    # Distinct keys only, so the join cannot fan out the orders DataFrame.
    known_customers = (
        customers_df.select(F.col(customer_id_column).alias("_known_customer_id"))
        .where(F.col("_known_customer_id").isNotNull())
        .distinct()
    )

    joined = orders_df.join(
        known_customers,
        orders_df[customer_id_column] == known_customers["_known_customer_id"],
        how="left",
    )

    status = (
        F.when(
            F.col("_known_customer_id").isNull(),
            F.lit(STATUS_INVALID_CUSTOMER),
        )
        .when(F.col(amount_column).isNull(), F.lit(STATUS_MISSING_AMOUNT))
        .otherwise(F.lit(VALID_QUALITY_STATUS))
    )

    return joined.withColumn("data_quality_status", status).drop("_known_customer_id")


def add_surrogate_key(df: DataFrame, source_column: str, key_column: str) -> DataFrame:
    """Derive a dimension surrogate key from a business key.

    This project maps the surrogate key 1:1 from the business key to keep the example
    readable. A production build would generate an independent sequence so that the key
    survives changes to the source system's identifiers.

    Args:
        df: Source dimension DataFrame.
        source_column: Business-key column, for example ``"customer_id"``.
        key_column: Name of the surrogate key to create, for example ``"customer_key"``.

    Returns:
        A DataFrame with the surrogate key column added as the first column.
    """
    with_key = df.withColumn(key_column, F.col(source_column).cast("int"))
    ordered = [key_column] + [c for c in with_key.columns if c != key_column]
    return with_key.select(*ordered)


def to_date_key(column: Column) -> Column:
    """Convert a date column into an integer ``yyyyMMdd`` date key.

    Args:
        column: A ``DATE`` or ``TIMESTAMP`` column expression.

    Returns:
        An ``INT`` column expression suitable for joining to ``dim_date``.
    """
    return F.date_format(column, "yyyyMMdd").cast("int")
