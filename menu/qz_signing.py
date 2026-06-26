"""QZ Tray request signing.

QZ Tray will print silently (no per-action security popup) only if the POS
page can prove it's a trusted site. The proof is a two-part handshake:

  1. The page hands QZ a *digital certificate* (public, served as-is).
  2. For every privileged call, QZ asks the page to *sign* a short payload;
     the page relays the payload here and we sign it with the matching
     private key. QZ verifies the signature against the certificate.

The private key authorises the *site*, not any particular print job, so the
only gate that matters here is that the caller is an authenticated POS user —
the views enforce that.

QZ Tray 2.1+ verifies signatures as RSA-PKCS#1 v1.5 over SHA-512, base64.

If the cert/key files are missing (not yet provisioned for a rollout), these
helpers return ``None`` and the front-end degrades to QZ's unsigned mode
(one allow-prompt per print) instead of breaking printing entirely.
"""

from __future__ import annotations

import base64
import functools

from django.conf import settings


@functools.lru_cache(maxsize=1)
def _load_private_key():
    """Load and cache the RSA private key, or None if unavailable/invalid."""
    path = getattr(settings, 'QZ_PRIVATE_KEY_PATH', '')
    if not path:
        return None
    try:
        with open(path, 'rb') as fh:
            pem = fh.read()
    except OSError:
        return None
    try:
        from cryptography.hazmat.primitives import serialization
        return serialization.load_pem_private_key(pem, password=None)
    except Exception:
        return None


def get_certificate() -> str | None:
    """Return the public digital certificate text QZ Tray should trust,
    or None if it hasn't been provisioned."""
    path = getattr(settings, 'QZ_CERT_PATH', '')
    if not path:
        return None
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            cert = fh.read().strip()
    except OSError:
        return None
    return cert or None


def sign_request(message: str) -> str | None:
    """Sign QZ Tray's request payload and return a base64 signature.

    Returns None when signing isn't configured, so the caller can tell QZ to
    fall back to unsigned mode rather than presenting a broken signature.
    """
    key = _load_private_key()
    if key is None:
        return None
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        signature = key.sign(
            message.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA512(),
        )
    except Exception:
        return None
    return base64.b64encode(signature).decode('ascii')


def signing_available() -> bool:
    """True when both cert and key are present, i.e. silent printing is live."""
    return get_certificate() is not None and _load_private_key() is not None
