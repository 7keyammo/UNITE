#!/usr/bin/env python3
from __future__ import annotations

import argparse

from core.kernel import DaQauntumKernel


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the DaQauntum v0.4.0 guided demo in Terminal")
    ap.add_argument("--speak", action="store_true", help="Speak each demo step using the configured local/system TTS")
    args = ap.parse_args()
    kernel = DaQauntumKernel()
    state = kernel.demo.start()
    while True:
        current = state.get("current") or {}
        print("\n" + "=" * 72)
        print(f"[{state.get('index',0)+1}/{state.get('total',0)}] {current.get('title','DaQauntum Demo')}")
        print(current.get("say", ""))
        if current.get("instruction"):
            print("\nTRY:", current["instruction"])
        print("READY:", "yes" if current.get("ready") else "not fully configured")
        if args.speak:
            try:
                kernel.demo.speak_current()
            except Exception as exc:
                print("Voice unavailable:", exc)
        if state.get("completed"):
            break
        try:
            command = input("\nPress Enter for next, 'q' to quit: ").strip().lower()
        except EOFError:
            command = "q"
        if command == "q":
            break
        state = kernel.demo.next()
        if state.get("completed"):
            print("\nDemo complete.")
            break


if __name__ == "__main__":
    main()
