from __future__ import annotations

import argparse
import asyncio
import ctypes
import tempfile
from pathlib import Path

import edge_tts


def mci(command: str) -> None:
    buffer = ctypes.create_unicode_buffer(255)
    result = ctypes.windll.winmm.mciSendStringW(command, buffer, 254, 0)
    if result != 0:
        raise RuntimeError(f"MCI command failed: {command} ({result})")


def edge_rate(value: int) -> str:
    value = max(-50, min(50, value * 6))
    sign = "+" if value >= 0 else ""
    return f"{sign}{value}%"


async def synthesize(text: str, voice: str, rate: int, output: Path) -> None:
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=edge_rate(rate))
    await communicate.save(str(output))


def play_mp3(path: Path) -> None:
    alias = "eseo_assistant_voice"
    escaped = str(path).replace('"', "")
    try:
        mci(f'open "{escaped}" type mpegvideo alias {alias}')
        mci(f"play {alias} wait")
    finally:
        try:
            mci(f"close {alias}")
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--voice", default="en-US-JennyNeural")
    parser.add_argument("--rate", type=int, default=-1)
    args = parser.parse_args()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as handle:
        output = Path(handle.name)
    try:
        asyncio.run(synthesize(args.text, args.voice, args.rate, output))
        play_mp3(output)
    finally:
        try:
            output.unlink(missing_ok=True)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
