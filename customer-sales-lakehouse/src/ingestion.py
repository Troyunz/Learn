"""Reusable ingestion helpers for the raw -> bronze hop.

The functions here deliberately avoid any business logic. Their only jobs are to read a
source file with an explicit schema and to stamp each row with audit metadata so that
every bronze record can be traced back to the file and run that produced it.
"""

from __future__ import annotations

from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StructType

from src.config import SPARK_APP_NAME, as_str


def get_spark_session(app_name: str = SPARK_APP_NAME) -> SparkSession:
    """Create (or reuse) the project's local SparkSession.

    ``getOrCreate`` means repeated calls inside one process return the same session,
    which is what the pipeline scripts and the pytest fixture both rely on.

    Args:
        app_name: Spark application name shown in the Spark UI and logs.

    Returns:
        An active :class:`~pyspark.sql.SparkSession`.
    """
    return (
        SparkSession.builder.appName(app_name)
        # Small dataset: 200 shuffle partitions would create hundreds of empty files.
        .config("spark.sql.shuffle.partitions", "4")
        # Fail fast on overflow/invalid casts rather than silently producing surprises.
        .config("spark.sql.session.timeZone", "UTC")
        .master("local[*]")
        .getOrCreate()
    )


def read_csv_to_df(
    spark: SparkSession,
    path: str | Path,
    schema: StructType,
    header: bool = True,
) -> DataFrame:
    """Read a CSV file into a DataFrame using an explicit schema.

    An explicit schema is used instead of ``inferSchema`` for three reasons:

    1. It is deterministic - a change in the source data cannot silently change types.
    2. It avoids an extra full scan of the file.
    3. Unparseable values become ``NULL`` (``PERMISSIVE`` mode) rather than aborting the
       job, which lets the quality framework report on them downstream.

    Args:
        spark: The active SparkSession.
        path: Path to the CSV file or folder of CSV files.
        schema: The schema to apply to the file.
        header: Whether the first line of the file is a header row.

    Returns:
        A DataFrame with exactly the columns declared in ``schema``.

    Example:
        >>> df = read_csv_to_df(spark, "data/raw/customers.csv", CUSTOMERS_SCHEMA)
    """
    return (
        spark.read.option("header", str(header).lower())
        .option("mode", "PERMISSIVE")
        # Treat a bare empty field as NULL rather than as a zero-length string.
        .option("nullValue", "")
        .option("emptyValue", "")
        .schema(schema)
        .csv(as_str(Path(path)))
    )


def add_audit_columns(df: DataFrame) -> DataFrame:
    """Stamp a DataFrame with the three standard bronze audit columns.

    Args:
        df: The freshly-read source DataFrame.

    Returns:
        The same DataFrame plus:

        - ``ingestion_timestamp`` - when this job ran.
        - ``source_file_name`` - the file each row was read from.
        - ``processing_date`` - the logical date of the pipeline run.
    """
    return (
        df.withColumn("ingestion_timestamp", F.current_timestamp())
        .withColumn("source_file_name", F.input_file_name())
        .withColumn("processing_date", F.current_date())
    )


def write_parquet(df: DataFrame, path: str | Path, mode: str = "overwrite") -> None:
    """Write a DataFrame to Parquet.

    ``overwrite`` is the default because every stage of this pipeline fully rebuilds its
    own output, which is what makes the pipeline idempotent.

    Args:
        df: The DataFrame to persist.
        path: Destination folder.
        mode: Spark save mode. Keep ``"overwrite"`` unless you have added merge logic.
    """
    df.write.mode(mode).parquet(as_str(Path(path)))


def read_parquet(spark: SparkSession, path: str | Path) -> DataFrame:
    """Read a Parquet folder produced by an earlier pipeline stage.

    Args:
        spark: The active SparkSession.
        path: Folder written by :func:`write_parquet`.

    Returns:
        The stored DataFrame.
    """
    return spark.read.parquet(as_str(Path(path)))
