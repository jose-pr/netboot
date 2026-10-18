"""netboot's logging helpers."""

import logging

import pytest

from netboot.logging import quiet_noisy_dependencies


@pytest.mark.parametrize(
    "name", ["urllib3.connectionpool", "asyncssh", "paramiko.transport"]
)
def test_quieting_lowers_each_noisy_library_to_warning(name):
    logger = logging.getLogger(name)
    before = logger.level
    try:
        logger.setLevel(logging.DEBUG)
        quiet_noisy_dependencies()
        assert logger.level == logging.WARNING
    finally:
        logger.setLevel(before)
