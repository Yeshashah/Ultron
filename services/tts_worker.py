"""Standalone text-to-speech helper process.

Ultron runs each utterance in this short-lived process so speech can be interrupted instantly
(by terminating the process) without leaving pyttsx3's audio engine in a broken state.

Input: one JSON object on stdin, e.g. {"mode": "speak", "text": "...", "rate": 175, "volume": 0.9, "voice": ""}
       or {"mode": "voices"} to print the installed voices as JSON.
"""

import json
import sys


def _select_voice(engine, wanted: str) -> None:
    wanted = (wanted or "").strip().lower()
    if not wanted:
        return
    for voice in engine.getProperty("voices") or []:
        if wanted == str(voice.id).lower() or wanted in str(voice.name).lower():
            engine.setProperty("voice", voice.id)
            return


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        print("invalid request", file=sys.stderr)
        return 2

    import pyttsx3

    engine = pyttsx3.init()
    if request.get("mode") == "voices":
        voices = [{"id": str(v.id), "name": str(v.name)} for v in engine.getProperty("voices") or []]
        print(json.dumps(voices))
        return 0

    text = str(request.get("text", "")).strip()
    if not text:
        return 0
    engine.setProperty("rate", int(request.get("rate", 175)))
    engine.setProperty("volume", float(request.get("volume", 0.9)))
    _select_voice(engine, str(request.get("voice", "")))
    engine.say(text)
    engine.runAndWait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
