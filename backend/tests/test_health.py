import httpx


async def test_health_returns_three_ok_flags():
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "db": True, "sandbox": True}
