from __future__ import annotations

import json
from pathlib import Path

from edge_smart_home.app.pipeline import ControlOutcome, TextControlPipeline
from edge_smart_home.common import DeviceState, SceneExecutionResult
from edge_smart_home.device import ConfirmationRequiredError, DeviceBackendError
from edge_smart_home.router import UnsupportedCommandError


def main() -> None:
    project_root = Path(__file__).resolve().parents[3]
    pipeline = TextControlPipeline.from_config_dir(project_root / "configs")
    print("Edge Smart Home Phase 0（输入“退出”结束）")
    while True:
        text = input("> ").strip()
        if text in {"退出", "exit", "quit"}:
            return
        if not text:
            continue
        try:
            outcome = pipeline.handle_text(text)
        except ConfirmationRequiredError as error:
            print(f"需要确认：{error}")
            if input("确认执行？[y/N] ").strip().lower() not in {"y", "yes"}:
                print("已取消")
                continue
            outcome = pipeline.handle_text(text, confirmed=True)
        except (UnsupportedCommandError, DeviceBackendError) as error:
            print(f"无法执行：{error}")
            continue
        print(json.dumps(_serialize_outcome(outcome), ensure_ascii=False))


def _serialize_outcome(outcome: ControlOutcome) -> dict:
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


if __name__ == "__main__":
    main()
