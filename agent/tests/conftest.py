import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs external services (DB/Ollama/bank); skipped by default")
