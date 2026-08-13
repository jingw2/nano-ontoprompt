"""Enforce the supported Python floor before any third-party import."""

import sys

MIN_SUPPORTED = (3, 11)
UNSUPPORTED_PYTHON_VERSION = "UNSUPPORTED_PYTHON_VERSION"


def require_supported_python():
    if sys.version_info[:2] < MIN_SUPPORTED:
        detected = ".".join(str(part) for part in sys.version_info[:3])
        required = ".".join(str(part) for part in MIN_SUPPORTED)
        raise SystemExit(
            f"{UNSUPPORTED_PYTHON_VERSION}: detected Python {detected}, required >= {required}"
        )
    return True


if __name__ == "__main__":
    require_supported_python()
