# generate_greeting.py
import asyncio
import queue as _q
import os
from voice.tts import init_tts, TTSSentenceStreamer
from dotenv import load_dotenv
load_dotenv()
init_tts(os.getenv("CARTESIA_API_KEY"))

async def main():
    chunks = []
    sentence_q = _q.Queue()
    sentence_q.put("Hey, I'm Alex. I help businesses handle customer enquiries instantly and convert more leads. Just curious — how are you currently managing your incoming messages or calls?")
    sentence_q.put(None)  # sentinel

    async def collect(chunk: bytes):
        chunks.append(chunk)

    streamer = TTSSentenceStreamer(on_audio_chunk=collect,voice_id="228fca29-3a0a-435c-8728-5cb483251068")
    await streamer.stream(sentence_q, asyncio.Event())

    with open("greeting_sales.pcm", "wb") as f:
        f.write(b"".join(chunks))
    print(f"Done — {sum(len(c) for c in chunks)} bytes")

asyncio.run(main())