import json

from sse_starlette.sse import ServerSentEvent


def token_event(content: str) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps({"content": content}), event="token")


def tool_call_event(tool_name: str, args: dict) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps({"tool": tool_name, "args": args}), event="tool_call")


def tool_result_event(tool_name: str, result: str) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps({"tool": tool_name, "result": result}), event="tool_result")


def approval_event(action: str, details: dict) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps({"action": action, "details": details}), event="approval_required")


def done_event(message_id: str, conversation_id: str) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps({"message_id": message_id, "conversation_id": conversation_id}), event="done")


def error_event(message: str) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps({"message": message}), event="error")


def status_event(message: str) -> ServerSentEvent:
    return ServerSentEvent(data=json.dumps({"message": message}), event="status")
