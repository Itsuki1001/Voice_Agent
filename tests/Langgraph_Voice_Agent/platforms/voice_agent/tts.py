"""
tts.py — TTS using ElevenLabs async streaming.
Pre-warms by starting synthesis on first sentence immediately,
then pipelines remaining sentences.
"""

import asyncio
import queue as _q
import re

from elevenlabs.client import AsyncElevenLabs

VOICE_ID           = "JBFqnCBsd6RMkjVDRZzb"
TTS_MODEL          = "eleven_multilingual_v2"
MIN_SENTENCE_CHARS = 4

_eleven: AsyncElevenLabs | None = None

def init_tts(api_key: str):
    global _eleven
    _eleven = AsyncElevenLabs(api_key=api_key)

_SENT_RE = re.compile(r'(?<=[.!?])\s+')

def split_sentences(buffer: str) -> tuple[list[str], str]:
    parts = _SENT_RE.split(buffer)
    if len(parts) <= 1:
        return [], buffer
    complete = [s for s in parts[:-1] if len(s.strip()) >= MIN_SENTENCE_CHARS]
    return complete, parts[-1]


async def _synthesise_and_stream(sentence: str, on_audio_chunk, cancel_event):
    """Async streaming synthesis for one sentence."""
    audio_stream = _eleven.text_to_speech.stream(
        text=sentence,
        voice_id=VOICE_ID,
        model_id=TTS_MODEL,
        output_format="pcm_24000",
    )
    async for chunk in audio_stream:
        if cancel_event.is_set():
            break
        if chunk:
            await on_audio_chunk(chunk)
            await asyncio.sleep(0)


class TTSSentenceStreamer:

    def __init__(self, on_audio_chunk):
        self.on_audio_chunk = on_audio_chunk

    async def stream(
        self,
        sentence_q: _q.Queue,
        cancel_event: asyncio.Event,
    ) -> list[str]:
        assert _eleven is not None, "Call init_tts(api_key) before using TTS."

        full_parts: list[str] = []

        while True:
            if cancel_event.is_set():
                # drain so LLM thread can exit
                while True:
                    try:
                        item = sentence_q.get_nowait()
                        if item is None:
                            break
                    except _q.Empty:
                        await asyncio.sleep(0.01)
                break

            try:
                sentence = sentence_q.get_nowait()
            except _q.Empty:
                await asyncio.sleep(0.01)
                continue

            if sentence is None:
                break

            full_parts.append(sentence)
            print(f"[TTS] synthesising: {sentence[:60]}…")

            try:
                await _synthesise_and_stream(
                    sentence, self.on_audio_chunk, cancel_event
                )
            except Exception as exc:
                print(f"[TTS ERROR] {exc}")

        return full_parts