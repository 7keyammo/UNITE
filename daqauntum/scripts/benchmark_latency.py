#!/usr/bin/env python3
"""Measure real conversational latency for DaQauntum on this machine.

TASKS.md P0.2 and P0.3 ask for measured numbers rather than impressions: the
time to first token and total turn time for each usable model route, so AUTO
and REALTIME can be tuned to the hardware actually running DaQauntum.

The benchmark issues real short-dialogue turns through the normal model router.
It changes no configuration and writes only to data/diagnostics/.

Examples:

    PYTHONPATH=. python scripts/benchmark_latency.py
    PYTHONPATH=. python scripts/benchmark_latency.py --turns 10 --providers ollama,claude_cli
    PYTHONPATH=. python scripts/benchmark_latency.py --audio sample.wav
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from core.kernel import DaQauntumKernel


# Short, self-contained prompts representative of everyday conversational turns.
# Deliberately not creative writing: this measures the route, not the task.
DEFAULT_PROMPTS = [
    "In one sentence, what is the capital of France?",
    "Say hello and nothing else.",
    "What is 17 times 4? Answer with the number only.",
    "Name one primary colour.",
    "In one short sentence, what does a thermostat do?",
    "Reply with the single word: ready.",
    "What day comes after Tuesday? One word.",
    "In one sentence, why is water wet?",
    "Give one example of a mammal. One word.",
    "In one short sentence, what is a battery for?",
]

SYSTEM_PROMPT = "You are a concise assistant. Answer in one short sentence unless told otherwise."


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def summarize(label: str, samples: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [s for s in samples if s.get("ok")]
    ttfts = [s["ttft_ms"] for s in ok if s.get("ttft_ms") is not None]
    totals = [s["total_ms"] for s in ok]
    return {
        "route": label,
        "turns": len(samples),
        "succeeded": len(ok),
        "failed": len(samples) - len(ok),
        "ttft_median_ms": round(statistics.median(ttfts), 1) if ttfts else None,
        "ttft_p95_ms": round(percentile(ttfts, 0.95), 1) if ttfts else None,
        "total_median_ms": round(statistics.median(totals), 1) if totals else None,
        "total_p95_ms": round(percentile(totals, 0.95), 1) if totals else None,
        "chars_per_second": (
            round(sum(s["chars"] for s in ok) / (sum(totals) / 1000.0), 1) if totals and sum(totals) else None
        ),
        "errors": sorted({str(s.get("error")) for s in samples if not s.get("ok")})[:3],
    }


def run_turn(kernel: DaQauntumKernel, provider: str, prompt: str) -> dict[str, Any]:
    """One streamed turn, timed from request to first token and to completion."""
    routing = {"preference": [provider], "exclude_providers": []}
    messages = [{"role": "user", "content": prompt}]
    started = time.perf_counter()
    first_token_at: float | None = None
    chars = 0
    used_provider = used_model = None
    try:
        for event in kernel.brain.stream_generate("executor", SYSTEM_PROMPT, messages, routing=routing):
            if event["type"] == "meta":
                used_provider, used_model = event.get("provider"), event.get("model")
            elif event["type"] == "delta":
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                chars += len(event.get("text", ""))
            elif event["type"] == "done":
                response = event["response"]
                used_provider = response.provider
                used_model = response.model
                if not chars:
                    chars = len(response.text or "")
                    # A non-streaming adapter returns everything at once, so
                    # first-token time equals total time rather than being lost.
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "total_ms": 0.0, "chars": 0}

    finished = time.perf_counter()
    return {
        "ok": True,
        "provider": used_provider,
        "model": used_model,
        # A route that silently fell back to another provider is reported, not
        # counted as a measurement of the route that was asked for.
        "requested_provider": provider,
        "fell_back": used_provider != provider,
        "ttft_ms": round((first_token_at - started) * 1000.0, 1) if first_token_at else None,
        "total_ms": round((finished - started) * 1000.0, 1),
        "chars": chars,
    }


def usable_providers(kernel: DaQauntumKernel) -> list[str]:
    found = []
    for provider in ("ollama", "claude_cli", "openai", "anthropic", "mock"):
        try:
            adapter = kernel.brain._build(provider)  # noqa: SLF001 - diagnostic introspection
            if adapter.available().available:
                found.append(provider)
        except Exception:
            continue
    return found


def measure_voice(kernel: DaQauntumKernel, audio_path: str | None) -> dict[str, Any]:
    """Measure local speech timing when the stack is actually configured."""
    result: dict[str, Any] = {}
    status = kernel.voice.status(refresh=True)
    result["stt_available"] = bool((status.get("stt") or {}).get("available"))
    result["tts_available"] = bool((status.get("tts") or {}).get("available"))

    if audio_path:
        path = Path(audio_path)
        if not path.exists():
            result["stt_error"] = f"Audio file not found: {audio_path}"
        elif not result["stt_available"]:
            result["stt_error"] = "Local STT is not configured; run scripts/setup_local_voice.py"
        else:
            started = time.perf_counter()
            try:
                text = kernel.voice.transcribe(path.read_bytes(), mime_type="audio/wav")
                result["stt_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
                result["stt_chars"] = len(str(text or ""))
            except Exception as exc:
                result["stt_error"] = f"{type(exc).__name__}: {exc}"
    return result


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# DaQauntum Latency Report",
        "",
        f"- Generated: {payload['generated_at']}",
        f"- Turns per route: {payload['turns_per_route']}",
        f"- Routes measured: {', '.join(payload['routes_measured']) or 'none'}",
        "",
        "## Conversational routes",
        "",
        "| Route | Turns | OK | TTFT median | TTFT p95 | Total median | Total p95 | chars/s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["results"]:
        lines.append(
            "| {route} | {turns} | {succeeded} | {ttft_median_ms} | {ttft_p95_ms} | "
            "{total_median_ms} | {total_p95_ms} | {chars_per_second} |".format(
                **{key: ("—" if row.get(key) is None else row.get(key)) for key in (
                    "route", "turns", "succeeded", "ttft_median_ms", "ttft_p95_ms",
                    "total_median_ms", "total_p95_ms", "chars_per_second")}
            )
        )
    lines += ["", "## Voice", ""]
    voice = payload.get("voice") or {}
    lines.append(f"- Local STT available: {voice.get('stt_available')}")
    lines.append(f"- TTS available: {voice.get('tts_available')}")
    if voice.get("stt_ms") is not None:
        lines.append(f"- STT time for supplied sample: {voice['stt_ms']} ms ({voice.get('stt_chars', 0)} chars)")
    if voice.get("stt_error"):
        lines.append(f"- STT not measured: {voice['stt_error']}")

    lines += ["", "## Measured spoken turns", ""]
    measured = payload.get("measured") or {}
    if not measured.get("turns"):
        lines += [
            "No spoken turns recorded yet. This section fills in once you hold a real voice",
            "conversation: the four stages are measured from the live duplex session, not",
            "simulated here.",
            "",
            "| Stage | Meaning |",
            "|---|---|",
            "| hear | speech end to final transcript |",
            "| think | final transcript to first model token |",
            "| speak | first model token to first audible reply |",
            "| total | the whole perceived turn |",
        ]
    else:
        lines += [
            f"- Turns recorded: {measured['turns']}",
            f"- Slowest stage: **{measured.get('slowest_stage') or 'unknown'}**",
            "",
            "| Stage | Median | p95 | Min | Max |",
            "|---|---|---|---|---|",
        ]
        for name, data in (measured.get("stages") or {}).items():
            lines.append(f"| {name} | {data['median_ms']} | {data['p95_ms']} | {data['min_ms']} | {data['max_ms']} |")
        perceived = measured.get("perceived")
        if perceived:
            lines += [
                "",
                f"- **Perceived latency** (speech end to first audible reply): "
                f"median {perceived['median_ms']} ms, p95 {perceived['p95_ms']} ms",
            ]

    lines += ["", "## Reading these numbers", ""]
    # The mock provider is an architecture fallback, not inference, so it must
    # never be recommended as a route however fast it looks.
    fastest = next(
        (r for r in payload["results"]
         if r.get("ttft_median_ms") is not None and not r["route"].startswith("mock")),
        None,
    )
    if fastest:
        lines.append(
            f"- Fastest first token: **{fastest['route']}** at {fastest['ttft_median_ms']} ms median. "
            "Consider making it the REALTIME/AUTO route for short dialogue."
        )
    else:
        lines.append(
            "- No real inference route was measured. Configure Ollama, the Claude CLI, or an API key; "
            "the mock provider is an architecture fallback and is not a usable route."
        )
    lines += [
        "- Time to first token drives how responsive a spoken reply feels; total time matters more for Deep tasks.",
        "- Keep Deep mode on the strongest route even if it is slower. Latency tuning should not cost answer quality.",
        "- A route marked as falling back did not actually run on the provider requested.",
        "",
        "_Runtime artifact. Regenerate with `PYTHONPATH=. python scripts/benchmark_latency.py`._",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark DaQauntum conversational latency")
    parser.add_argument("--turns", type=int, default=10, help="Short turns per route (default 10)")
    parser.add_argument("--providers", default="", help="Comma-separated providers; default is every available one")
    parser.add_argument("--audio", default="", help="Optional WAV file to measure local STT time")
    parser.add_argument("--include-mock", action="store_true", help="Also benchmark the mock provider")
    parser.add_argument("--output", default="data/diagnostics/LATENCY_REPORT.md")
    parser.add_argument("--json", action="store_true", help="Print the raw measurements as JSON")
    args = parser.parse_args()

    kernel = DaQauntumKernel()
    if args.providers.strip():
        providers = [p.strip().lower() for p in args.providers.split(",") if p.strip()]
    else:
        providers = usable_providers(kernel)
        if not args.include_mock:
            providers = [p for p in providers if p != "mock"] or providers

    turns = max(1, min(int(args.turns), 50))
    print(f"DaQauntum latency benchmark — {turns} turn(s) across: {', '.join(providers) or 'nothing available'}")
    print("=" * 72)

    results: list[dict[str, Any]] = []
    raw: dict[str, list[dict[str, Any]]] = {}
    for provider in providers:
        samples: list[dict[str, Any]] = []
        print(f"\n{provider}:", end=" ", flush=True)
        for index in range(turns):
            sample = run_turn(kernel, provider, DEFAULT_PROMPTS[index % len(DEFAULT_PROMPTS)])
            samples.append(sample)
            print("." if sample.get("ok") else "x", end="", flush=True)
        raw[provider] = samples
        summary = summarize(provider, samples)
        fell_back = sum(1 for s in samples if s.get("fell_back"))
        if fell_back:
            summary["route"] = f"{provider} (fell back {fell_back}/{turns})"
        results.append(summary)
        print(
            f"  TTFT median {summary['ttft_median_ms']} ms · total median {summary['total_median_ms']} ms"
            f" · {summary['succeeded']}/{summary['turns']} ok"
        )

    results.sort(key=lambda row: (row["ttft_median_ms"] is None, row["ttft_median_ms"] or 0))
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "turns_per_route": turns,
        "routes_measured": providers,
        "results": results,
        "voice": measure_voice(kernel, args.audio or None),
        # Real spoken turns, recorded by the duplex session. Model-only
        # benchmarking cannot produce these: there is no microphone in a loop
        # that never listened to anything.
        "measured": kernel.latency.stats(),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(payload), encoding="utf-8")
    measured = payload["measured"]
    if measured.get("turns"):
        print(f"\n\nMeasured spoken turns: {measured['turns']} · slowest stage: {measured.get('slowest_stage')}")
        for name, data in (measured.get("stages") or {}).items():
            print(f"  {name:11s} median {data['median_ms']:>8} ms   p95 {data['p95_ms']:>8} ms")
    else:
        print("\n\nNo spoken turns recorded yet — hold a voice conversation to fill in the "
              "hear/think/speak breakdown.")
    print(f"\nWrote {output}")
    if args.json:
        print(json.dumps({"summary": payload, "samples": raw}, indent=2, default=str))


if __name__ == "__main__":
    main()
