#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from core.kernel import DaQauntumKernel


def ollama_status() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"running": True, "models": [m.get("name") for m in data.get("models", [])]}
    except Exception as exc:
        return {"running": False, "models": [], "error": str(exc)}


def main() -> None:
    ap = argparse.ArgumentParser(description="DaQauntum v0.4.0 first-run readiness wizard")
    ap.add_argument("--pull-model", default="", help="Explicitly run `ollama pull MODEL` if Ollama is installed")
    args = ap.parse_args()

    if args.pull_model:
        if not shutil.which("ollama"):
            raise SystemExit("ollama CLI is not installed")
        print(f"Pulling local model {args.pull_model} ...")
        subprocess.run(["ollama", "pull", args.pull_model], check=True)

    kernel = DaQauntumKernel()
    model = ollama_status()
    voice = kernel.voice.status(refresh=True)
    presence = kernel.presence.passive_snapshot(save=True)
    readiness = kernel.demo.readiness()

    print("\nDaQauntum v0.4.0 First Run")
    print("=" * 52)
    print(f"Readiness score: {readiness['score']}%")
    print("\nBRAIN")
    print("OpenAI key:", "configured" if os.getenv("OPENAI_API_KEY") else "not configured")
    print("Anthropic key:", "configured" if os.getenv("ANTHROPIC_API_KEY") else "not configured")
    print("Claude CLI:", shutil.which("claude") or "not installed")
    print("Ollama:", "running" if model["running"] else "not running")
    if model["models"]:
        print("Local models:", ", ".join(model["models"][:12]))
    print("\nVOICE")
    print(json.dumps(voice, indent=2, default=str))
    print("\nPRESENCE")
    print(kernel.presence.describe(presence))
    print("\nNEXT")
    if not model["running"] and not os.getenv("OPENAI_API_KEY") and not os.getenv("ANTHROPIC_API_KEY") and not shutil.which("claude"):
        print("- Configure a real reasoning provider. Recommended local route: install/start Ollama, then run this script with --pull-model <model>.")
    if not (voice.get("stt") or {}).get("available"):
        print("- Local STT is not ready. Install requirements-voice.txt and run scripts/setup_local_voice.py.")
    print("- Launch: python daqauntum_gui.py")
    print("- Open the ▷ Guided Demo tab and press Start demo.")
    print("- Open ◎ Presence to inspect sensors and explicitly scan nearby radios.")


if __name__ == "__main__":
    main()
