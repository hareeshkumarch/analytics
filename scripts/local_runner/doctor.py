import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.domain.limits import DEFAULT_DATA_LIMITS


def main() -> int:
    report = {
        "python_version": sys.version.split()[0],
        "docker_available": shutil.which("docker") is not None,
        "master_key_configured": bool(os.environ.get("APP_MASTER_KEY")),
        "ingress_bind": "127.0.0.1:8080",
        "safe_limits": DEFAULT_DATA_LIMITS.to_safe_dict(),
        "providers_configured": {
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
            "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "gemini": bool(os.environ.get("GEMINI_API_KEY")),
            "groq": bool(os.environ.get("GROQ_API_KEY")),
            "xai": bool(os.environ.get("XAI_API_KEY")),
            "kimi": bool(os.environ.get("MOONSHOT_API_KEY")),
            "nvidia_nim": bool(os.environ.get("NVIDIA_NIM_API_KEY")),
        },
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
