#!/usr/bin/env python3
from __future__ import annotations

import os
from core.kernel import DaQauntumKernel


def main() -> None:
    kernel = DaQauntumKernel()
    p = kernel.perception.stats()
    c = kernel.computer.status()
    print("DaQauntum v0.4.0 Eyes + Hands doctor")
    print(f"frames: {p['frames']}")
    print(f"vision local model: {p['vision']['ollama'].get('model') or 'not configured'}")
    print(f"OPENAI_API_KEY: {'configured' if os.getenv('OPENAI_API_KEY') else 'not configured'}")
    print(f"ANTHROPIC_API_KEY: {'configured' if os.getenv('ANTHROPIC_API_KEY') else 'not configured'}")
    oi = c.get('open_interpreter') or {}
    print(f"Open Interpreter: {oi.get('detail', 'unknown')}")
    print(f"computer autonomy: {c.get('autonomy')}")
    print(f"permission level: L{kernel.permissions.level}")
    print("Browser screen/camera permission can only be tested after opening the GUI.")


if __name__ == '__main__':
    main()
