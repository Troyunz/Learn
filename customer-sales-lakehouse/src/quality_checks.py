"""Reusable data-quality checks.

Every check follows the same contract: it takes one or more DataFrames and returns a
``CheckResult`` - a ``(check_name, status, failed_count)`` triple where ``status`` is
``PASS`` when ``failed_count`` is zero and ``FAIL`` otherwise.

Because ``CheckResult`` is a :class:`~typing.NamedTuple`, it unpacks like a plain tuple
(``name, status, count = result``) while still supporting readable attribute access.

The rules are documented in ``docs/data_quality_rules.md``.
"""

from __future__ import annotations

from typing import NamedTuple

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from src.config import VALID_ORDER_STATUSES

#: Status string used when a check finds no offending rows.
PASS: str = "PASS"

#: Status string used when a check finds at least one offending row.
FAIL: str = "FAIL"


class CheckResult(NamedTuple):
    """Outcome of a single data-quality check.

    Attributes:
        check_name: Stable identifier for the check, used as the result table key.
        status: ``PASS`` or ``FAIL``.
        failed_count: Number of rows (or keys) that violated the rule.
    """

    check_name: str
    status: str
    failed_count: int


def _result(check_name: str, failed_count: int) -> CheckResult:
    """Build a :class:`CheckResult`, deriving the status from the failure count.

    Args:
        check_name: Identifier for the check.
        failed_count: Number of offending rows.

    Returns:
        A populated :class:`CheckResult`.
    """
    return CheckResult(
        check_name=check_name,
        status=PASS if failed_count == 0 else FAIL,
        failed_count=int(failed_count),
    )


def check_duplicate_order_id(
    orders_df: DataFrame, key_column: str = "order_id"
) -> CheckResult:
    """Verify that the order business key is unique.

    Counts *keys* that appear more than once, not the total number of excess rows, so a
    key appearing three times contributes ``1`` to the failure count.

    Args:
        orders_df: Orders DataFrame to inspect.
        key_column: Name of the business-key column.

    Returns:
        A :class:`CheckResult` named ``duplicate_order_id``.
    """
    duplicate_keys = (
        orders_df.groupBy(key_column).count().filter(F.col("count") > 1).count()
    )
    return _result("duplicate_order_id", duplicate_keys)


def check_missing_order_amount(
    orders_df: DataFrame, amount_column: str = "amount"
) -> CheckResult:
    """Verify that every order carries a monetary amount.

    A ``NULL`` amount is silently skipped by ``SUM()``, which understates revenue
    without raising an error - hence the explicit check.

    Args:
        orders_df: Orders DataFrame to inspect.
        amount_column: Name of the monetary column.

    Returns:
        A :class:`CheckResult` named ``missing_order_amount``.
    """
    missing = orders_df.filter(F.col(amount_column).isNull()).count()
    return _result("missing_order_amount", missing)


def check_invalid_customer_reference(
    orders_df: DataFrame,
    customers_df: DataFrame,
    order_customer_column: str = "customer_id",
    customer_key_column: str = "customer_id",
) -> CheckResult:
    """Verify referential integrity from orders to customers.

    Implemented as a left anti join, which counts order rows with no matching customer
    without fanning out on duplicate customer keys.

    Args:
        orders_df: Orders DataFrame to inspect.
        customers_df: Customer dimension used as the lookup.
        order_customer_column: Foreign-key column on ``orders_df``.
        customer_key_column: Business-key column on ``customers_df``.

    Returns:
        A :class:`CheckResult` named ``invalid_customer_reference``.
    """
    known_customers = (
        customers_df.select(F.col(customer_key_column).alias("_known_customer_id"))
        .where(F.col("_known_customer_id").isNotNull())
        .distinct()
    )

    orphans = orders_df.join(
        known_customers,
        orders_df[order_customer_column] == known_customers["_known_customer_id"],
        how="left_anti",
    ).count()

    return _result("invalid_customer_reference", orphans)


def check_invalid_status(
    orders_df: DataFrame,
    status_column: str = "status",
    allowed_statuses: tuple[str, ...] = VALID_ORDER_STATUSES,
) -> CheckResult:
    """Verify that every order status falls inside the allowed domain.

    ``NULL`` statuses count as failures: an order whose lifecycle state is unknown
    cannot be classified as revenue or not.

    Args:
        orders_df: Orders DataFrame to inspect.
        status_column: Name of the status column.
        allowed_statuses: The permitted domain values.

    Returns:
        A :class:`CheckResult` named ``invalid_status``.
    """
    invalid = orders_df.filter(
        F.col(status_column).isNull()
        | ~F.col(status_column).isin(list(allowed_statuses))
    ).count()
    return _result("invalid_status", invalid)


def run_all_checks(orders_df: DataFrame, customers_df: DataFrame) -> list[CheckResult]:
    """Execute the full standard check suite in a fixed, reproducible order.

    Args:
        orders_df: Orders DataFrame to inspect.
        customers_df: Customers DataFrame used for the referential check.

    Returns:
        One :class:`CheckResult` per registered check.
    """
    return [
        check_duplicate_order_id(orders_df),
        check_missing_order_amount(orders_df),
        check_invalid_customer_reference(orders_df, customers_df),
        check_invalid_status(orders_df),
    ]


#: Names of the checks produced by :func:`run_all_checks`, in order.
ALL_CHECKS: tuple[str, ...] = (
    "duplicate_order_id",
    "missing_order_amount",
    "invalid_customer_reference",
    "invalid_status",
)


def summarise(results: list[CheckResult]) -> str:
    """Render check results as a fixed-width table for console output.

    Args:
        results: Results returned by :func:`run_all_checks`.

    Returns:
        A multi-line string ready to be logged.
    """
    header = f"{'check_name':<30} {'status':<8} {'failed_count':>12}"
    separator = "-" * len(header)
    rows = [
        f"{r.check_name:<30} {r.status:<8} {r.failed_count:>12}" for r in results
    ]
    return "\n".join([separator, header, separator, *rows, separator])
