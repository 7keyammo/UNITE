from __future__ import annotations

import io
import wave

from voice.local_voice import LocalVoiceEngine


def tiny_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


def main() -> None:
    engine = LocalVoiceEngine(
        {
            "stt": {"backend": "mock", "mock_transcript": "DaQauntum local voice engine works"},
            "tts": {"backend": "mock"},
            "wake": {"enabled": False, "phrase": "daqauntum"},
        }
    )
    status = engine.status(refresh=True)
    assert status["stt"]["available"] and status["stt"]["backend"] == "mock", status
    assert status["tts"]["available"] and status["tts"]["backend"] == "mock", status
    result = engine.transcribe_wav_bytes(tiny_wav())
    assert result["local"] and "local voice engine works" in result["text"], result
    spoken = engine.speak("DaQauntum voice test")
    assert spoken["ok"] and spoken["local"], spoken
    try:
        engine.transcribe_wav_bytes(b"not-a-wav")
        raise AssertionError("invalid WAV should have failed")
    except ValueError:
        pass
    print("DaQauntum v0.4.0 local voice smoke test: PASS")


if __name__ == "__main__":
    main()
