"""Freeze local code/config/model identities; never overwrite an earlier freeze."""
import argparse
import json
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from edge_smart_home.benchmark.run_record import build_run_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifact", action="append", default=[], type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--ollama-model", help="Read /api/tags only; never load or generate")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; choose a new run ID and path")
    record = build_run_record(Path(__file__).resolve().parents[1], args.run_id,
                              artifacts=args.artifact)
    if args.ollama_model:
        try:
            with urlopen(args.ollama_base_url.rstrip("/") + "/api/tags", timeout=2) as response:
                models = json.load(response).get("models", [])
            match = next((item for item in models if item.get("name") == args.ollama_model), None)
            record["ollama_identity"] = {
                "model": args.ollama_model, "source": "read_only_api_tags",
                "digest": match.get("digest") if match else None,
                "status": "resolved" if match and match.get("digest") else "not_found",
            }
            record["ollama_digest"] = record["ollama_identity"]["digest"]
            record["notes"] = "Local file inventory and read-only Ollama tag identity; no inference."
        except (OSError, ValueError, URLError) as error:
            record["ollama_identity"] = {"model": args.ollama_model, "status": "unavailable",
                                         "error_type": type(error).__name__, "digest": None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(record, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(args.output)


if __name__ == "__main__":
    main()
