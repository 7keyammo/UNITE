#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import platform
import shutil
from pathlib import Path

import yaml

from voice.local_voice import LocalVoiceEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Check or prepare DaQauntum's fully-local voice stack")
    parser.add_argument("--download-whisper", metavar="MODEL", help="Download a faster-whisper model into data/models (e.g. base.en)")
    parser.add_argument("--configure", action="store_true", help="Write the downloaded Whisper model path into config.yaml")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    print(f"DaQauntum local voice setup ({platform.system()} {platform.machine()})")
    print(f"Project: {root}")

    if args.download_whisper:
        if importlib.util.find_spec("faster_whisper") is None:
            raise SystemExit("faster-whisper is not installed. Run: pip install -r requirements-voice.txt")
        from faster_whisper import download_model

        safe = args.download_whisper.replace("/", "-")
        target = root / "data" / "models" / f"faster-whisper-{safe}"
        target.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {args.download_whisper} to {target} ...")
        path = download_model(args.download_whisper, output_dir=str(target))
        print(f"Whisper model ready: {path}")
        if args.configure:
            config_path = root / "config.yaml"
            if config_path.exists():
                config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            else:
                example = root / "config.example.yaml"
                config = yaml.safe_load(example.read_text(encoding="utf-8")) or {}
            voice = config.setdefault("voice", {})
            stt = voice.setdefault("stt", {})
            stt["backend"] = "faster_whisper"
            fw = stt.setdefault("faster_whisper", {})
            fw["model"] = str(target)
            fw["allow_model_download"] = False
            config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            print(f"Updated {config_path} with the local model path.")
        else:
            print("Set this in config.yaml:")
            print("voice:")
            print("  stt:")
            print("    backend: faster_whisper")
            print("    faster_whisper:")
            print(f"      model: {target}")
            print("      allow_model_download: false")

    cfg = {
        "stt": {
            "backend": "auto",
            "faster_whisper": {"model": "base.en", "allow_model_download": False},
            "whisper_cpp": {"binary": "whisper-cli", "model": ""},
        },
        "tts": {"backend": "auto", "system": {}, "piper": {"binary": "piper", "model": ""}},
    }
    engine = LocalVoiceEngine(cfg)
    status = engine.status(refresh=True)
    print("\nDetected:")
    print(f"  faster-whisper package: {'yes' if importlib.util.find_spec('faster_whisper') else 'no'}")
    print(f"  whisper-cli: {shutil.which('whisper-cli') or 'not found'}")
    print(f"  piper: {shutil.which('piper') or 'not found'}")
    print(f"  local STT: {status['stt']['backend']} / available={status['stt']['available']}")
    print(f"  local TTS: {status['tts']['backend']} / available={status['tts']['available']}")
    print("\nFor local Whisper STT:")
    print("  pip install -r requirements-voice.txt")
    print("  python scripts/setup_local_voice.py --download-whisper base.en --configure")
    print("The --configure flag writes the local model path into config.yaml automatically.")


if __name__ == "__main__":
    main()
