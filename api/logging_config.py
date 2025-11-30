import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Union


def configure_logging(log_dir: Union[str, Path], level: str = "INFO") -> None:
    """Configure application-wide logging to stdout and rotating files."""

    log_directory = Path(log_dir)
    log_directory.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=log_level)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    # Root logger handler writing to file
    app_log_path = log_directory / "app.log"
    app_file_handler = RotatingFileHandler(
        app_log_path, maxBytes=5 * 1024 * 1024, backupCount=5
    )
    app_file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) and h.baseFilename == str(app_log_path)
               for h in root_logger.handlers):
        root_logger.addHandler(app_file_handler)

    # SQLAlchemy engine logs for debugging SQL queries
    sqlalchemy_log_path = log_directory / "sqlalchemy.log"
    sql_handler = RotatingFileHandler(
        sqlalchemy_log_path, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    sql_handler.setFormatter(formatter)
    sql_logger = logging.getLogger("sqlalchemy.engine")
    if not any(isinstance(h, RotatingFileHandler) and h.baseFilename == str(sqlalchemy_log_path)
               for h in sql_logger.handlers):
        sql_logger.setLevel(logging.INFO)
        sql_logger.addHandler(sql_handler)

    # Ensure stdout/stderr also receive logs
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

