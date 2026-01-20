"""
Custom logging configuration for HACCP application.
Provides file and console handlers with configurable levels.
"""
import os
import logging
from pathlib import Path
from typing import Optional


def configure_global_logger(
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    logging_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    log_file: str = "./haccp.log",
) -> logging.Logger:
    """
    Configure the global logger with file and console handlers.

    :param console_level: Logging level for console output (default: INFO)
    :param file_level: Logging level for file output (default: DEBUG)
    :param logging_format: Format string for log messages
    :param log_file: Path to log file (default: ./haccp.log in repo root)
    :return: Configured root logger instance
    """
    # Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # Avoid adding duplicate handlers
    if logger.handlers:
        return logger

    formatter = logging.Formatter(logging_format)

    # Ensure parent directory exists
    log_path = Path(log_file)
    if log_path.parent != Path(".") and not log_path.parent.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)

    # File handler
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(file_level)
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(console_level)
    logger.addHandler(console_handler)

    logger.debug(f"Global logger configured - console: {console_level}, file: {file_level}")

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get or create a logger with the specified name.

    :param name: Logger name (typically __name__ of the calling module)
    :return: Logger instance
    """
    return logging.getLogger(name)


def parse_log_level(level: str) -> int:
    """
    Parse a log level string to its numeric value.

    :param level: Log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    :return: Numeric log level
    """
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return level_map.get(level.upper(), logging.INFO)
