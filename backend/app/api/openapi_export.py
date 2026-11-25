import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.api.main import create_app
from app.bootstrap.container import build_container

DEFAULT_TARGET = Path(__file__).resolve().parents[3] / "frontend" / "openapi.json"


def openapi_document() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as root:
        document: dict[str, Any] = create_app(build_container(artifact_root=root)).openapi()
    return document


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    target.write_text(render(openapi_document()), encoding="utf-8")
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()
