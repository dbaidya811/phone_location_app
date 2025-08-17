"""
WSGI entry point for production servers (Render/Gunicorn).

This attempts imports that work whether the Render Root Directory is the
repository root or the package directory `phone_location_app/`.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Ensure repo root is importable
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    # Case A: Root Directory = repo root
    from phone_location_app.app import app  # type: ignore
except ModuleNotFoundError:
    try:
        # Case B: Root Directory = phone_location_app/
        # In that case, `app.py` is importable directly.
        from app import app  # type: ignore
    except ModuleNotFoundError:
        # Last resort: add the package directory explicitly
        pkg_dir = os.path.join(BASE_DIR, "phone_location_app")
        if pkg_dir not in sys.path:
            sys.path.insert(0, pkg_dir)
        from app import app  # type: ignore
