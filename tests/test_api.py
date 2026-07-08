import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import init_db
from app.main import app


@pytest.fixture(autouse=True)
async def setup():
    await init_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "environment" in data


class TestTaskEndpoints:
    @pytest.mark.asyncio
    async def test_create_task(self, client):
        resp = await client.post("/api/tasks", json={"prompt": "hello agent"})
        assert resp.status_code == 201
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_create_task_empty_prompt(self, client):
        resp = await client.post("/api/tasks", json={"prompt": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_task_no_body(self, client):
        resp = await client.post("/api/tasks")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_tasks(self, client):
        await client.post("/api/tasks", json={"prompt": "task 1"})
        await client.post("/api/tasks", json={"prompt": "task 2"})
        resp = await client.get("/api/tasks")
        assert resp.status_code == 200
        tasks = resp.json()
        assert len(tasks) >= 2

    @pytest.mark.asyncio
    async def test_list_tasks_pagination(self, client):
        for i in range(5):
            await client.post("/api/tasks", json={"prompt": f"task {i}"})
        resp = await client.get("/api/tasks?limit=2&offset=0")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_get_task(self, client):
        create_resp = await client.post("/api/tasks", json={"prompt": "get me"})
        task_id = create_resp.json()["task_id"]

        resp = await client.get(f"/api/tasks/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id
        assert data["prompt"] == "get me"

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, client):
        resp = await client.get("/api/tasks/NONEXISTENT_TASK_ID_XYZ")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_task(self, client):
        """Cancel a task that hasn't been picked up by background execution yet."""
        # Directly create via service to avoid background execution
        from app.services.task_service import create_task
        task_id = await create_task(prompt="cancel me")

        resp = await client.post(f"/api/tasks/{task_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, client):
        resp = await client.post("/api/tasks/NOPE_000000000000000/cancel")
        assert resp.status_code == 400
