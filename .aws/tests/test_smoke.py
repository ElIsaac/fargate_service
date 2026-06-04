"""
Smoke E2E del contenedor fargate_service.

Verifica que el contenedor recién pulleado de ECR arranca y expone los
contratos básicos de la app FastAPI:

  - /health responde 200 con {"status": "ok"}.
  - / responde 200 con el HTML de la landing.
  - El catch-all responde 200 con {"ok": true} ante cualquier ruta/método.

TESTING_BASE_URL lo inyecta buildspec-testing.yml en env.variables,
resuelto a http://localhost:$APP_PORT durante el build.
"""

import os

import requests

BASE_URL = os.environ["TESTING_BASE_URL"].rstrip("/")
TIMEOUT = 5  # seg


def test_health_returns_ok():
    res = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok"}


def test_root_returns_html():
    res = requests.get(f"{BASE_URL}/", timeout=TIMEOUT)
    assert res.status_code == 200, res.text
    assert "FastAPI funcionando correctamente" in res.text


def test_catch_all_accepts_any_route():
    res = requests.post(
        f"{BASE_URL}/ruta/que/no/existe",
        json={"hola": "mundo"},
        timeout=TIMEOUT,
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True}
