#!/usr/bin/env python3
from __future__ import annotations

import json

from core.kernel import DaQauntumKernel


def main() -> None:
    kernel = DaQauntumKernel()
    snapshot = kernel.presence.passive_snapshot(save=True)
    print("DaQauntum v0.4.0 Presence doctor")
    print("=" * 44)
    print(kernel.presence.describe(snapshot))
    print("\nCapabilities")
    print(json.dumps(kernel.presence.stats(), indent=2))
    print("\nActive radio discovery is intentionally NOT run by this doctor.")
    print("Use the Presence GUI or natural-language commands to explicitly scan Wi-Fi/Bluetooth.")


if __name__ == "__main__":
    main()
