"""The piskie logger, plus best-effort quieting of chatty optional deps."""

import logging

LOGGER = logging.getLogger("PISKIE")

# Quiet third-party loggers if those libraries end up in the process (e.g. a repo
# backend pulls in urllib3/paramiko). Setting a level does not import anything.
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
logging.getLogger("paramiko.transport").setLevel(logging.WARNING)

try:  # pragma: no cover - only when urllib3 is actually installed
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass
