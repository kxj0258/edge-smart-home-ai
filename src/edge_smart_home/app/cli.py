from __future__ import annotations

import json
from pathlib import Path

from edge_smart_home.app.pipeline import TextControlPipeline
from edge_smart_home.device import DeviceBackendError
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
            state = pipeline.handle_text(text)
            print(json.dumps({
                "device_id": state.device_id,
                "available": state.available,
                "attributes": dict(state.attributes),
                "updated_at": state.updated_at.isoformat(),
            }, ensure_ascii=False))
        except (UnsupportedCommandError, DeviceBackendError) as error:
            print(f"无法执行：{error}")


if __name__ == "__main__":
    main()

