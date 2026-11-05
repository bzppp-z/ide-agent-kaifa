import uuid
from datetime import datetime

from sqlalchemy import inspect, select, text


async def test_four_tables_are_created(db_setup):
    from app.db import engine

    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())

    assert {"sessions", "messages", "tool_calls", "usage"} <= set(tables)


async def test_insert_and_select_session_roundtrip(db_session):
    from app.models.session import Session as SessionModel

    from app.db import utcnow

    session_id = str(uuid.uuid4())
    now = datetime(2026, 11, 5, 3, 0, 0)
    row = SessionModel(
        id=session_id,
        demo_repo="todo-app",
        container_id="C:/tmp/devflow-workspace",
        created_at=now,
        last_active_at=now,
    )
    db_session.add(row)
    await db_session.commit()

    loaded = (
        await db_session.execute(select(SessionModel).where(SessionModel.id == session_id))
    ).scalar_one()

    assert loaded.id == session_id
    assert loaded.demo_repo == "todo-app"
    assert loaded.container_id == "C:/tmp/devflow-workspace"
    assert loaded.created_at == now
    assert loaded.last_active_at == now
    assert isinstance(loaded.created_at, datetime)
    assert utcnow().tzinfo is None


async def test_message_tool_call_usage_models_roundtrip(db_session):
    from app.models.message import Message
    from app.models.session import Session as SessionModel
    from app.models.tool_call import ToolCall
    from app.models.usage import Usage

    session_id = str(uuid.uuid4())
    now = datetime(2026, 11, 5, 4, 0, 0)
    db_session.add(SessionModel(id=session_id, demo_repo="express-demo", created_at=now, last_active_at=now))
    db_session.add(Message(id=str(uuid.uuid4()), session_id=session_id, role="user", content="hi", created_at=now))
    db_session.add(
        ToolCall(
            id=str(uuid.uuid4()),
            session_id=session_id,
            tool_name="read",
            input_json={"path": "src/a.js"},
            result_json={"content": "const a = 1;"},
            ok=True,
            duration_ms=12,
            created_at=now,
        )
    )
    db_session.add(
        Usage(
            id=str(uuid.uuid4()),
            session_id=session_id,
            run_id="run-1",
            total_tokens=1200,
            total_steps=3,
            created_at=now,
        )
    )
    await db_session.commit()

    message = (await db_session.execute(select(Message))).scalar_one()
    assert message.session_id == session_id
    assert message.role == "user"
    assert message.content == "hi"

    tool_call = (await db_session.execute(select(ToolCall))).scalar_one()
    assert tool_call.tool_name == "read"
    assert tool_call.input_json == {"path": "src/a.js"}
    assert tool_call.result_json == {"content": "const a = 1;"}
    assert tool_call.ok is True
    assert tool_call.duration_ms == 12

    usage = (await db_session.execute(select(Usage))).scalar_one()
    assert usage.session_id == session_id
    assert usage.run_id == "run-1"
    assert usage.total_tokens == 1200
    assert usage.total_steps == 3

    await db_session.execute(text("SELECT 1"))
