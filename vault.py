"""
Aion Secrets Manager

Encrypted credential storage. API keys, passwords, and other
sensitive values are encrypted on disk and decrypted in memory
at runtime.

- Encrypted file: data/secrets.enc
- Master key: AION_SECRET_KEY environment variable
- If no master key is set, one is generated and saved to data/.master_key
  (This is for development convenience. In production, use the env var
  and delete the file.)

Credentials are managed through the web UI settings page.
"""

import json
import os
import logging
from pathlib import Path
from base64 import urlsafe_b64encode

from cryptography.fernet import Fernet

from config import DATA_DIR

logger = logging.getLogger("aion.secrets")

SECRETS_FILE = DATA_DIR / "secrets.enc"
MASTER_KEY_FILE = DATA_DIR / ".master_key"

# In-memory decrypted secrets
_secrets: dict[str, str] = {}
_fernet: Fernet | None = None


def _get_master_key() -> bytes:
    """
    Get the master encryption key.

    Priority:
    1. AION_SECRET_KEY environment variable
    2. .master_key file (development convenience)
    3. Generate a new key and save it
    """
    # Check environment variable first
    env_key = os.environ.get("AION_SECRET_KEY")
    if env_key:
        return env_key.encode()

    # Check for saved key file
    if MASTER_KEY_FILE.exists():
        return MASTER_KEY_FILE.read_bytes().strip()

    # Generate a new key
    key = Fernet.generate_key()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MASTER_KEY_FILE.write_bytes(key)
    os.chmod(str(MASTER_KEY_FILE), 0o600)
    logger.info("Generated new master key (saved to .master_key)")
    return key


def init_secrets():
    """Initialize the secrets manager. Load and decrypt existing secrets."""
    global _fernet, _secrets

    _fernet = Fernet(_get_master_key())

    if SECRETS_FILE.exists():
        try:
            encrypted = SECRETS_FILE.read_bytes()
            decrypted = _fernet.decrypt(encrypted)
            _secrets = json.loads(decrypted.decode())
            logger.info(f"Loaded {len(_secrets)} secrets")
        except Exception as e:
            logger.error(f"Failed to decrypt secrets: {e}")
            _secrets = {}
    else:
        _secrets = {}
        logger.info("No secrets file found, starting fresh")


def _save():
    """Encrypt and save secrets to disk."""
    if _fernet is None:
        init_secrets()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    plaintext = json.dumps(_secrets).encode()
    encrypted = _fernet.encrypt(plaintext)
    SECRETS_FILE.write_bytes(encrypted)
    os.chmod(str(SECRETS_FILE), 0o600)


def get(key: str) -> str | None:
    """Get a secret by key name. Returns None if not found."""
    if not _secrets and SECRETS_FILE.exists():
        init_secrets()
    return _secrets.get(key)


def set_secret(key: str, value: str):
    """Set a secret. Encrypts and saves immediately."""
    _secrets[key] = value
    _save()
    logger.info(f"Secret '{key}' saved")


def delete(key: str) -> bool:
    """Delete a secret. Returns True if it existed."""
    if key in _secrets:
        del _secrets[key]
        _save()
        logger.info(f"Secret '{key}' deleted")
        return True
    return False


def list_keys() -> list[str]:
    """List all secret key names (not values)."""
    if not _secrets and SECRETS_FILE.exists():
        init_secrets()
    return list(_secrets.keys())


def has(key: str) -> bool:
    """Check if a secret exists."""
    if not _secrets and SECRETS_FILE.exists():
        init_secrets()
    return key in _secrets
