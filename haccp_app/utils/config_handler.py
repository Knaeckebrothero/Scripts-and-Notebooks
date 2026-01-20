"""
Configuration handler for HACCP application.
Manages INI file-based configuration with auto-creation of defaults.
"""
import os
import configparser
import logging
from pathlib import Path
from typing import Optional, Any

log = logging.getLogger(__name__)


class ConfigHandler:
    """
    Singleton configuration handler for INI file-based settings.
    Auto-creates default configuration on first run.
    """

    _instance: Optional["ConfigHandler"] = None

    @classmethod
    def get_instance(cls, config_path: str = None) -> "ConfigHandler":
        """Get or create the singleton configuration instance."""
        if cls._instance is None:
            path = config_path or str(Path(__file__).parent.parent / "haccp.ini")
            cls._instance = cls(path)
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        cls._instance = None

    def __init__(self, config_path: str):
        """
        Initialize the configuration handler.

        :param config_path: Path to the INI configuration file
        """
        self.config_path = config_path
        self.config = configparser.ConfigParser()

        if not os.path.exists(config_path):
            self._create_default_config()
        else:
            self.load()

    def _create_default_config(self):
        """Create default configuration file with HACCP-specific settings."""
        self.config["APP"] = {
            "db_path": "./haccp.db",
            "log_path": "./logs/",
            "log_level_console": "INFO",
            "log_level_file": "DEBUG",
        }

        self.config["HACCP"] = {
            "fridge_temp_min": "0.0",
            "fridge_temp_max": "7.0",
            "freezer_temp_min": "-25.0",
            "freezer_temp_max": "-18.0",
            "expiry_warn_days": "3",
        }

        self.config["CLEANING"] = {
            "kaffeemaschine_frequency": "1",
            "teestation_frequency": "1",
            "buffet_frequency": "1",
            "eierstation_frequency": "1",
        }

        self.config["AUTH"] = {
            "session_timeout_hours": "8",
            "max_login_attempts": "5",
            "lockout_minutes": "15",
        }

        self.save()
        log.info(f"Created default configuration at {self.config_path}")

    def load(self):
        """Load configuration from file."""
        try:
            self.config.read(self.config_path)
            log.debug(f"Loaded configuration from {self.config_path}")

            # Ensure required sections exist
            required_sections = ["APP", "HACCP", "CLEANING", "AUTH"]
            for section in required_sections:
                if section not in self.config:
                    self.config[section] = {}

        except Exception as e:
            log.error(f"Error loading configuration: {e}")
            self._create_default_config()

    def save(self):
        """Save configuration to file."""
        try:
            with open(self.config_path, "w") as f:
                self.config.write(f)
            log.debug(f"Saved configuration to {self.config_path}")
        except Exception as e:
            log.error(f"Error saving configuration: {e}")

    def get(self, section: str, key: str, default: Any = None) -> Optional[str]:
        """
        Get a configuration value.

        :param section: Configuration section name
        :param key: Key within the section
        :param default: Default value if not found
        :return: Configuration value or default
        """
        try:
            if section in self.config and key in self.config[section]:
                return self.config[section][key]
            return default
        except Exception as e:
            log.error(f"Error getting config {section}.{key}: {e}")
            return default

    def get_int(self, section: str, key: str, default: int = 0) -> int:
        """Get an integer configuration value."""
        value = self.get(section, key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def get_float(self, section: str, key: str, default: float = 0.0) -> float:
        """Get a float configuration value."""
        value = self.get(section, key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    def get_bool(self, section: str, key: str, default: bool = False) -> bool:
        """Get a boolean configuration value."""
        value = self.get(section, key)
        if value is None:
            return default
        return value.lower() in ("true", "yes", "1", "on")

    def set(self, section: str, key: str, value: Any) -> bool:
        """
        Set a configuration value.

        :param section: Configuration section name
        :param key: Key within the section
        :param value: Value to set
        :return: True if successful
        """
        try:
            if section not in self.config:
                self.config[section] = {}
            self.config[section][key] = str(value)
            self.save()
            log.debug(f"Set config {section}.{key} = {value}")
            return True
        except Exception as e:
            log.error(f"Error setting config {section}.{key}: {e}")
            return False
