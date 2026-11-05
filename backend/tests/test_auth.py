"""JWT 会话 token 与用量记录测试。"""

import uuid

from sqlalchemy import select

from app.services.auth import create_session_token, record_usage, verify_session_token

SECRET = "unit-test-secret"


def test_create_then_verify_roundtrip():
    session_id = str(uuid.uuid4())

    token = create_session_token(session_id, SECRET, expire_hours=24)

    assert verify_session_token(token, SECRET) == session_id


def test_expired_token_returns_none():
    token = create_session_token(str(uuid.uuid4()), SECRET, expire_hours=-1)

    assert verify_session_token(token, SECRET) is None


def test_tampered_token_returns_none():
    session_id = str(uuid.uuid4())
    token = create_session_token(session_id, SECRET, expire_hours=24)
    replacement = "A" if token[-1] != "A" else "B"
    tampered = token[:-1] + replacement

    assert verify_session_token(tampered, SECRET) is None
    assert verify_session_token(token.replace(".", "..", 1), SECRET) is None


def test_wrong_secret_returns_none():
    token = create_session_token(str(uuid.uuid4()), SECRET, expire_hours=24)

    assert verify_session_token(token, "other-secret") is None


def test_garbage_token_returns_none():
    assert verify_session_token("not-a-jwt", SECRET) is None
    assert verify_session_token("", SECRET) is None


async def test_record_usage_writes_row(db_session):
    from app.models.session import Session as SessionModel
    from app.models.usage import Usage

    session_id = str(uuid.uuid4())
    db_session.add(SessionModel(id=session_id, demo_repo="todo-app"))
    await db_session.commit()

    await record_usage(db_session, session_id, "run-1", total_tokens=450, total_steps=2)

    row = (
        await db_session.execute(select(Usage).where(Usage.session_id == session_id))
    ).scalar_one()
    assert row.run_id == "run-1"
    assert row.total_tokens == 450
    assert row.total_steps == 2
    assert len(row.id) == 36
