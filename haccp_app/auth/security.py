"""
Security functions for HACCP authentication.
Password hashing, session key generation, and IP tracking.
"""
import os
import hashlib
import secrets
import string
import logging

import streamlit as st
from streamlit import runtime
from streamlit.runtime.scriptrunner import get_script_run_ctx

logger = logging.getLogger(__name__)


def get_client_ip() -> str:
    """
    Retrieve the IP address of the client making a request.
    Returns 'unknown' if IP cannot be determined.
    """
    try:
        ctx = get_script_run_ctx()
        if ctx is None:
            logger.warning("No script run context available")
            return "unknown"

        session_info = runtime.get_instance().get_client(ctx.session_id)
        if session_info is None:
            logger.warning("No session info available")
            return "unknown"

        return session_info.request.remote_ip
    except Exception as e:
        logger.error(f"Error getting client IP: {e}")
        return "unknown"


def hash_password(password: str, salt: bytes | str = None) -> tuple[str, str]:
    """
    Hash a password using PBKDF2-HMAC-SHA256 with 100,000 iterations.

    :param password: The password to hash
    :param salt: Optional salt (bytes or hex string). If None, generates new salt.
    :return: Tuple of (password_hash_hex, salt_hex)
    """
    if salt is None:
        salt = os.urandom(32)  # 256 bits
    elif isinstance(salt, str):
        salt = bytes.fromhex(salt)

    if isinstance(password, str):
        password = password.encode("utf-8")

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password,
        salt,
        100000,  # iterations
    )

    return password_hash.hex(), salt.hex()


def verify_password(stored_hash: str, stored_salt: str, provided_password: str) -> bool:
    """
    Verify a password against stored hash and salt.

    :param stored_hash: The stored password hash (hex string)
    :param stored_salt: The stored salt (hex string)
    :param provided_password: The password to verify
    :return: True if password matches, False otherwise
    """
    salt = bytes.fromhex(stored_salt)
    new_hash, _ = hash_password(provided_password, salt)
    return secrets.compare_digest(new_hash, stored_hash)


def generate_session_key(length: int = 32) -> str:
    """
    Generate a cryptographically secure session key.

    :param length: Number of bytes (will produce 2*length hex characters)
    :return: Hexadecimal session key string
    """
    return secrets.token_hex(length)


def generate_secure_password(length: int = 12) -> str:
    """
    Generate a secure random password with mixed character types.

    :param length: Password length (minimum 4)
    :return: Random password string
    """
    lowercase = string.ascii_lowercase
    uppercase = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%^&*"

    # Ensure at least one of each type
    password = [
        secrets.choice(lowercase),
        secrets.choice(uppercase),
        secrets.choice(digits),
        secrets.choice(special),
    ]

    # Fill remaining length
    all_chars = lowercase + uppercase + digits + special
    for _ in range(length - 4):
        password.append(secrets.choice(all_chars))

    # Shuffle to avoid predictable positions
    secrets.SystemRandom().shuffle(password)

    return "".join(password)
