"""
Configuración de pytest para la suite de smoke E2E.

Falla rápido si TESTING_BASE_URL no fue inyectado por buildspec-testing.yml.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def require_base_url():
    if not os.environ.get("TESTING_BASE_URL"):
        pytest.fail("TESTING_BASE_URL no está definido en el entorno", pytrace=False)
