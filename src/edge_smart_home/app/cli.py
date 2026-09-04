from __future__ import annotations

import json
from pathlib import Path

from edge_smart_home.app.orchestrator import AssistantOrchestrator, AssistantResponse
from edge_smart_home.app.pipeline import ControlOutcome
from edge_smart_home.common import DeviceState, SceneExecutionResult


def main() -> None:
    project_root = Path(__file__).resolve().parents[3]
    orchestrator = AssistantOrchestrator.from_config_dir(project_root / "configs")
    try:
        latency_ms = orchestrator.preload_model()
    except Exception as error:
        print(f"小兴模型预热失败，将在需要时安全降级：{type(error).__name__}: {error}")
    else:
        if latency_ms is not None:
            print(f"小兴模型已预热：{latency_ms:.1f} ms")
    print("小兴（Edge AI Smart Home Assistant，输入“退出”结束）")
    while True:
        text = input("> ").strip()
        if text in {"退出", "exit", "quit"}:
            return
        if not text:
            continue
        response = orchestrator.handle_text(text)
        if response.status == "confirmation_required":
            print(f"需要确认：{response.error}")
            if input("确认执行？[y/N] ").strip().lower() not in {"y", "yes"}:
                print("已取消")
                continue
            response = orchestrator.handle_text(text, confirmed=True)
        if response.outcome is None:
            print(json.dumps(serialize_response(response), ensure_ascii=False))
            continue
        print(json.dumps(serialize_response(response), ensure_ascii=False))


def serialize_outcome(outcome: ControlOutcome) -> dict:
    if isinstance(outcome, DeviceState):
        return {
            "type": "device_state",
            "device_id": outcome.device_id,
            "available": outcome.available,
            "attributes": dict(outcome.attributes),
            "updated_at": outcome.updated_at.isoformat(),
        }
    return {
        "type": "scene_result",
        "scene_id": outcome.scene_id,
        "succeeded": outcome.succeeded,
        "results": [
            {
                "device_id": result.command.device_id,
                "action": result.command.action,
                "succeeded": result.succeeded,
                "attributes": dict(result.state.attributes) if result.state else None,
                "error": result.error,
            }
            for result in outcome.results
        ],
    }


_serialize_outcome = serialize_outcome


def serialize_response(response: AssistantResponse) -> dict:
    payload = {
        "status": response.status,
        "source": response.event.source.value,
        "label": response.event.label,
        "event_id": response.event.event_id,
        "processing_ms": response.processing_ms,
        "path": response.path,
        "provider": response.provider,
        "llm_calls": response.llm_calls,
        "llm_latency_ms": response.llm_latency_ms,
    }
    if response.confirmation_message is not None:
        payload["confirmation_message"] = response.confirmation_message
    if response.outcome is not None:
        payload["result"] = serialize_outcome(response.outcome)
    if response.error is not None:
        payload["error"] = response.error
    if response.intelligent_result is not None:
        payload["intelligent_path"] = {
            "kind": response.intelligent_result.kind.value,
            "message": response.intelligent_result.message,
            "reason": response.intelligent_result.reason,
            "commands": [
                {
                    "device_id": command.device_id,
                    "action": command.action,
                    "arguments": dict(command.arguments),
                }
                for command in response.intelligent_result.commands
            ],
        }
    return payload


if __name__ == "__main__":
    main()
