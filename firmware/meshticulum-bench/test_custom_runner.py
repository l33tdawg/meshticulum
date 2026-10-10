"""Prevent PlatformIO firmware-only builds fetching an unused test framework.

Host-native fault tests are executed by pytest with clang++; a PlatformIO test
invocation must fail explicitly instead of implying target hardware was tested.
"""
from platformio.test.runners.base import TestRunnerBase


class CustomTestRunner(TestRunnerBase):
    EXTRA_LIB_DEPS = []

    def setup(self):
        raise RuntimeError('Run scripts/tests/test_meshticulum_bench_serial.py for host tests; no target test runner is installed.')
