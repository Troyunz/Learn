"""Environment configuration and path constants for every layer of the lakehouse.

Settings are resolved once, at import time, with this precedence:

1. Environment variables (``LAKEHOUSE_DATA_ROOT``, ``LAKEHOUSE_LOG_LEVEL``) - highest.
2. ``config/<environment>.yml``, selected by ``LAKEHOUSE_ENV`` (default ``dev``).
3. Built-in defaults (``<project root>/data``, ``INFO``).

All paths are resolved relative to the repository root, so scripts behave the same
whether they are launched from the project root, from an IDE, or from a test runner.
A relative ``data_root`` in a YAML file (such as ``./data``) is resolved against the
project root rather than the current working directory.

No credentials or connection secrets are stored here or in the YAML files. Secrets must
come from environment variables or a secret manager.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Root locations
# ---------------------------------------------------------------------------

#: Repository root (the folder containing ``src/``, ``notebooks/``, ``data/``).
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

#: Folder holding the per-environment YAML files.
CONFIG_DIR: Path = PROJECT_ROOT / "config"

#: Active environment name. Set ``LAKEHOUSE_ENV=prod`` to load ``config/prod.yml``.
ENVIRONMENT: str = os.environ.get("LAKEHOUSE_ENV", "dev")


def load_environment_config(environment: str = ENVIRONMENT) -> dict[str, Any]:
    """Load ``config/<environment>.yml`` and return it as a dictionary.

    Missing files and a missing ``pyyaml`` install are tolerated: the project must still
    run on built-in defaults, so configuration is an enhancement rather than a hard
    dependency.

    Args:
        environment: Environment name, for example ``"dev"`` or ``"prod"``.

    Returns:
        The parsed YAML mapping, or an empty dict if it could not be loaded.

    Example:
        >>> load_environment_config("dev")["log_level"]
        'INFO'
    """
    config_file = CONFIG_DIR / f"{environment}.yml"
    if not config_file.is_file():
        return {}

    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a pinned requirement
        return {}

    with config_file.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    return loaded if isinstance(loaded, dict) else {}


#: Contents of the active environment's YAML file.
ENV_CONFIG: dict[str, Any] = load_environment_config()


def _resolve_data_root() -> Path:
    """Resolve ``DATA_ROOT`` from the environment, then YAML, then the default.

    Returns:
        An absolute path to the lakehouse data root.
    """
    configured = os.environ.get("LAKEHOUSE_DATA_ROOT") or ENV_CONFIG.get("data_root")
    if not configured:
        return PROJECT_ROOT / "data"

    candidate = Path(str(configured))
    # A relative data_root (e.g. "./data") belongs to the project, not the shell's cwd.
    return candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate).resolve()


#: Root of all lakehouse data.
DATA_ROOT: Path = _resolve_data_root()

#: Logging verbosity for the whole project.
LOG_LEVEL: str = str(
    os.environ.get("LAKEHOUSE_LOG_LEVEL") or ENV_CONFIG.get("log_level") or "INFO"
).upper()

# Publish the resolved level so `logging_utils.get_logger` picks it up even when it is
# imported before this module by some other entry point.
os.environ.setdefault("LAKEHOUSE_LOG_LEVEL", LOG_LEVEL)

# ---------------------------------------------------------------------------
# Medallion layers
# ---------------------------------------------------------------------------

RAW_DIR: Path = DATA_ROOT / "raw"
BRONZE_DIR: Path = DATA_ROOT / "bronze"
SILVER_DIR: Path = DATA_ROOT / "silver"
GOLD_DIR: Path = DATA_ROOT / "gold"
QUALITY_RESULTS_DIR: Path = DATA_ROOT / "quality_results"

# ---------------------------------------------------------------------------
# Raw source files
# ---------------------------------------------------------------------------

RAW_CUSTOMERS: Path = RAW_DIR / "customers.csv"
RAW_PRODUCTS: Path = RAW_DIR / "products.csv"
RAW_ORDERS: Path = RAW_DIR / "orders.csv"

# ---------------------------------------------------------------------------
# Bronze tables
# ---------------------------------------------------------------------------

BRONZE_CUSTOMERS: Path = BRONZE_DIR / "customers"
BRONZE_PRODUCTS: Path = BRONZE_DIR / "products"
BRONZE_ORDERS: Path = BRONZE_DIR / "orders"

# ---------------------------------------------------------------------------
# Silver tables
# ---------------------------------------------------------------------------

SILVER_CUSTOMERS: Path = SILVER_DIR / "customers"
SILVER_PRODUCTS: Path = SILVER_DIR / "products"
SILVER_ORDERS: Path = SILVER_DIR / "orders"

# ---------------------------------------------------------------------------
# Gold tables (star schema)
# ---------------------------------------------------------------------------

GOLD_DIM_CUSTOMER: Path = GOLD_DIR / "dim_customer"
GOLD_DIM_PRODUCT: Path = GOLD_DIR / "dim_product"
GOLD_DIM_DATE: Path = GOLD_DIR / "dim_date"
GOLD_FACT_SALES: Path = GOLD_DIR / "fact_sales"

# ---------------------------------------------------------------------------
# Application constants
# ---------------------------------------------------------------------------

#: Name used for every SparkSession created by this project.
SPARK_APP_NAME: str = "customer-sales-lakehouse"

#: Allowed domain for ``orders.status`` after standardisation.
VALID_ORDER_STATUSES: tuple[str, ...] = ("Completed", "Cancelled", "Pending")

#: The only order status that represents recognised revenue.
REVENUE_STATUS: str = "Completed"

#: ``data_quality_status`` value required for a row to reach ``fact_sales``.
VALID_QUALITY_STATUS: str = "Valid"


def as_str(path: Path) -> str:
    """Return a POSIX-style string path, which Spark accepts on every platform.

    Spark's Hadoop filesystem layer treats backslashes as escape characters, so a raw
    Windows path such as ``C:\\data\\bronze`` can be mis-parsed. Converting to forward
    slashes keeps the same code working on Windows, macOS and Linux.

    Args:
        path: The filesystem path to convert.

    Returns:
        The path rendered with forward slashes.
    """
    return path.as_posix()


def ensure_directories() -> None:
    """Create every lakehouse directory if it does not already exist.

    Safe to call repeatedly; existing directories are left untouched.
    """
    for directory in (
        RAW_DIR,
        BRONZE_DIR,
        SILVER_DIR,
        GOLD_DIR,
        QUALITY_RESULTS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
