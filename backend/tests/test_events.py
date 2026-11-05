import json

from pydantic import TypeAdapter

from app.core.events import (
    DoneEvent,
    ErrorEvent,
    MessageDeltaEvent,
    PatchProposedEvent,
    RunOutputEvent,
    SSEEvent,
    ToolCallEvent,
    ToolResultEvent,
)


def test_message_delta_sse_format():
    event = MessageDeltaEvent(content="hi")
    assert event.to_sse_str() == 'event: message.delta\ndata: {"content":"hi"}\n\n'


def test_tool_call_payload_roundtrips():
    event = ToolCallEvent(call_id="c1", tool_name="read", arguments={"path": "src/a.ts"})
    raw = event.to_sse_str()
    assert raw.startswith("event: tool.call\ndata: ")
    assert raw.endswith("\n\n")
    payload = json.loads(raw.split("data: ", 1)[1].strip())
    assert payload == {"call_id": "c1", "tool_name": "read", "arguments": {"path": "src/a.ts"}}


def test_all_event_types_are_covered_by_union():
    events = [
        MessageDeltaEvent(content="x"),
        ToolCallEvent(call_id="c", tool_name="list"),
        ToolResultEvent(call_id="c", tool_name="list", ok=True),
        PatchProposedEvent(patch_id="p", path="a.txt", search="a", replace="b"),
        RunOutputEvent(chunk="out"),
        ErrorEvent(message="boom"),
        DoneEvent(run_id="r1"),
    ]
    expected_types = {
        "message.delta",
        "tool.call",
        "tool.result",
        "patch.proposed",
        "run.output",
        "error",
        "done",
    }
    assert {event.type for event in events} == expected_types

    adapter = TypeAdapter(SSEEvent)
    for event in events:
        parsed = adapter.validate_python(json.loads(event.model_dump_json()))
        assert parsed.type == event.type
