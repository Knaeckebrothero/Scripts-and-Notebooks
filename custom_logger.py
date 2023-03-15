"""
This is a custom logger for python projects.
https://docs.python.org/3/library/logging.html?highlight=logger#module-logging
"""

import logging

# Basic configuration for logging.
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")


# Defines a custom logger, login into a log file.
def configure_custom_logger():
    logger = logging.getLogger(__name__)
    handler = logging.FileHandler("log.log")
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
