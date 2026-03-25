from httpx import ASGITransport, AsyncClient
import pytest

from app.main import app


@pytest.mark.anyio
async def test_healthz_reports_missing_env() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "SPATIALREAL_AVATAR_APP_ID" in payload["missing"]
