"""真实 PostgreSQL：会话隔离、原子澄清、到期、并发及重启状态。"""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.workspace import COOKIE, Workspace

pytestmark = pytest.mark.integration


def wait(client, run_id):
    for _ in range(150):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] != "running":
            return run
        time.sleep(0.04)
    raise AssertionError("run did not finish")


def create(client, headers, question):
    response = client.post("/api/runs", json={"question": question}, headers=headers)
    assert response.status_code == 202, response.text
    return wait(client, response.json()["run_id"])


@pytest.fixture
def client():
    with TestClient(app) as client:
        session = client.get("/api/session")
        assert session.status_code == 200
        assert "HttpOnly" in session.headers["set-cookie"]
        assert "SameSite=lax" in session.headers["set-cookie"]
        headers = {"x-csrf-token": session.json()["csrf_token"]}
        yield client, headers
        workspace = app.state.workspace
        with workspace.pool.connection() as conn:
            owners = conn.execute(
                "SELECT owner_id FROM app_state.sessions WHERE token_hash=%s",
                (hashlib.sha256(client.cookies[COOKIE].encode()).hexdigest(),),
            ).fetchall()
            for owner in owners:
                ids = conn.execute(
                    "SELECT run_id FROM app_state.runs WHERE owner_id=%s", (owner["owner_id"],)
                ).fetchall()
                conn.execute("DELETE FROM app_state.runs WHERE owner_id=%s", (owner["owner_id"],))
                conn.execute(
                    "DELETE FROM app_state.sessions WHERE owner_id=%s", (owner["owner_id"],)
                )
                # 测试只清理自身创建的 checkpoint，不改演示或其他会话。
                for row in ids:
                    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                        conn.execute(
                            f"DELETE FROM app_state.{table} WHERE thread_id=%s",
                            (f"shoplens:{row['run_id']}",),
                        )


def test_session_access_csrf_and_input(client):
    first, headers = client
    run = create(first, headers, "9月各渠道实付金额")
    assert run["status"] == "succeeded"
    assert "owner_id" not in run and "metric_context" not in run
    assert run["report"]["chart"]["values"] == [
        row[-1] for row in run["report"]["tables"][0]["rows"]
    ]
    assert first.get(f"/api/runs/{run['run_id']}").json()["report"] == run["report"]
    other = TestClient(app)  # 独立 Cookie jar，共用已经启动的单个服务器。
    assert other.get(f"/api/runs/{run['run_id']}").status_code == 401
    info = other.get("/api/session").json()
    other_headers = {"x-csrf-token": info["csrf_token"]}
    assert other.get("/api/runs").json()["runs"] == []
    assert other.get(f"/api/runs/{run['run_id']}").status_code == 404
    assert (
        other.post(
            f"/api/runs/{run['run_id']}/clarify",
            json={"text": "实付", "revision": 0},
            headers=other_headers,
        ).status_code
        == 404
    )
    assert (
        other.post(f"/api/runs/{run['run_id']}/retry", json={}, headers=other_headers).status_code
        == 404
    )
    assert first.post("/api/runs", json={"question": "9月订单数"}).status_code == 403
    assert (
        first.post(
            "/api/runs",
            json={"question": "9月订单数"},
            headers={**headers, "origin": "https://untrusted.invalid"},
        ).status_code
        == 403
    )
    assert (
        first.post(
            "/api/runs",
            json={"question": "9月订单数", "owner_id": "chosen", "thread_id": "chosen"},
            headers=headers,
        ).status_code
        == 422
    )
    assert first.post("/api/runs", json={"question": "  "}, headers=headers).status_code == 422
    with app.state.workspace.pool.connection() as conn:
        conn.execute(
            "DELETE FROM app_state.sessions WHERE token_hash=%s",
            (hashlib.sha256(other.cookies[COOKIE].encode()).hexdigest(),),
        )
    other.close()


def test_clarification_claim_is_atomic(client):
    http, headers = client
    run = create(http, headers, "9月销售额是多少？")
    assert run["status"] == "awaiting_clarification"
    body = {"text": "实付金额", "revision": run["revision"]}
    path = f"/api/runs/{run['run_id']}/clarify"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: http.post(path, json=body, headers=headers), range(2)))
    assert sorted(r.status_code for r in responses) == [202, 409]
    done = wait(http, run["run_id"])
    assert done["status"] == "succeeded"
    assert done["clarifications"] == ["实付金额"]
    assert sum(e["node"] == "clarify" for e in done["events"]) == 1
    assert http.post(path, json=body, headers=headers).status_code == 409


def test_expired_clarification_and_failure(client):
    http, headers = client
    run = create(http, headers, "9月销售额是多少？")
    with app.state.workspace.pool.connection() as conn:
        conn.execute(
            "UPDATE app_state.runs SET clarification_expires_at=now()-interval '1 minute' WHERE run_id=%s",
            (run["run_id"],),
        )
    assert (
        http.post(
            f"/api/runs/{run['run_id']}/clarify",
            json={"text": "实付金额", "revision": run["revision"]},
            headers=headers,
        ).status_code
        == 409
    )
    retried = http.post(f"/api/runs/{run['run_id']}/retry", json={}, headers=headers)
    assert retried.status_code == 202
    assert retried.json()["run_id"] != run["run_id"]
    wait(http, retried.json()["run_id"])
    failed = create(http, headers, "2026-09-10 到 2026-09-01 实付金额")
    assert failed["status"] == "failed"
    rejected = create(http, headers, "预测10月利润")
    assert rejected["status"] == "rejected"


def test_single_process_capacity_and_role_permissions(client):
    http, headers = client
    workspace = app.state.workspace
    with pytest.raises(RuntimeError, match="one API process"):
        Workspace(get_settings())
    assert workspace.slots.acquire(False) and workspace.slots.acquire(False)
    try:
        assert (
            http.post("/api/runs", json={"question": "9月订单数"}, headers=headers).status_code
            == 429
        )
    finally:
        workspace.slots.release()
        workspace.slots.release()
    with psycopg.connect(get_settings().dsn("reader"), autocommit=True) as conn:
        for table in ("sessions", "runs", "events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"SELECT * FROM app_state.{table}")
