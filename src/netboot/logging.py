"""The netboot logger, plus opt-in quieting of chatty optional dependencies."""

import logging

#: The package logger. Named for the import package so it sits in the ordinary
#: hierarchy: `logging.getLogger("netboot")` reaches it, a `netboot.<child>`
#: logger inherits from it, and the CLI's -v/-q (which set the level of the
#: logger named by `Pixie_._logger_name_`) actually affect it.
LOGGER = logging.getLogger("netboot")


def quiet_noisy_dependencies(insecure_warnings: bool = False) -> None:
    """Turn down third-party loggers that are chatty at INFO.

    Not called on import: a library that reconfigures logging (or disables a
    security warning) for the whole process just because it was imported takes
    a decision that belongs to the application. The `pixie` CLI calls this; an
    embedding application opts in, or does not.

    With ``insecure_warnings=True`` this also silences urllib3's
    ``InsecureRequestWarning`` process-wide, which is why it is off by default.
    """
    for name in ("urllib3.connectionpool", "paramiko.transport"):
        logging.getLogger(name).setLevel(logging.WARNING)

    if insecure_warnings:
        try:  # pragma: no cover - only when urllib3 is installed
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
