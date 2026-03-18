import asyncio
import sys
import os
import re
import json
import uuid
import base64
import struct
import threading
import queue as _q
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from sarvamai import AsyncSarvamAI
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv

load_dotenv()

SARVAM_API_KEY     = os.getenv("SARVAM_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from graph.graph_voice import graph

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
MIN_INPUT_WORDS    = 2
LLM_DEBOUNCE       = 0
BARGE_IN_RMS       = 0.08
SAMPLE_RATE        = 16000
CHUNK_DURATION     = 0.1
SILENCE_HOLD       = 0.4
HESITATION_WORDS   = {"um", "uh", "hmm", "like"}
MIN_SENTENCE_CHARS = 20           # don't flush tiny fragments to TTS alone

VOICE_ID  = "JBFqnCBsd6RMkjVDRZzb"
TTS_MODEL = "eleven_multilingual_v2"
# ─────────────────────────────────────────────

eleven = ElevenLabs(api_key=ELEVENLABS_API_KEY)
app    = FastAPI()

with open(os.path.join(os.path.dirname(__file__), "voice_ui.html"), encoding="utf-8") as f:
    HTML = f.read()

@app.get("/")
async def index():
    return HTMLResponse(HTML)


# ── Text helpers ──────────────────────────────────────────────────────────────

def clean(text: str) -> str:
    text = re.sub(r'http\S+', '', text)
    text = re.sub(r'[•\*#`]', '', text)
    return re.sub(r'\n', ' ', text).strip()

def only_hesitation(text: str) -> bool:
    return bool(text) and all(w in HESITATION_WORDS for w in text.lower().split())

def is_sentence_complete(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if text[-1] in ".!?":
        return True
    if len(text.split()) >= 6:
        return True
    return False

# ── Audio helpers ─────────────────────────────────────────────────────────────

def add_wav_header(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    data_size = len(pcm_bytes)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", data_size + 36, b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate,
        sample_rate * 2, 2, 16,
        b"data", data_size,
    )
    return header + pcm_bytes

def check_rms(audio_bytes: bytes) -> float:
    samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) > 0 else 0.0

# ── Sentence splitter ─────────────────────────────────────────────────────────

_SENT_RE = re.compile(r'(?<=[.!?])\s+')

# ── LLM sentence streamer (runs in executor thread) ───────────────────────────

def stream_graph_sentences(transcript: str, thread_id: str,
                            sentence_q: _q.Queue, cancel_flag: threading.Event):
    """
    Streams LLM tokens, splits on sentence boundaries, pushes each complete
    sentence onto sentence_q.  Pushes None as sentinel when done.
    """
    config = {"configurable": {"thread_id": thread_id}}
    buffer = ""
    try:
        for chunk, _ in graph.stream(
            {"messages": transcript}, config, stream_mode="messages"
        ):
            if cancel_flag.is_set():
                break
            if hasattr(chunk, "content") and chunk.content:
                if type(chunk).__name__ in ("AIMessageChunk", "AIMessage"):
                    buffer += chunk.content
                    parts = _SENT_RE.split(buffer)
                    if len(parts) > 1:
                        for sentence in parts[:-1]:
                            s = clean(sentence)
                            if len(s) >= MIN_SENTENCE_CHARS:
                                sentence_q.put(s)
                        buffer = parts[-1]
        # flush remainder
        if buffer.strip() and not cancel_flag.is_set():
            r = clean(buffer.strip())
            if r:
                sentence_q.put(r)
    finally:
        sentence_q.put(None)   # sentinel


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(browser_ws: WebSocket):
    await browser_ws.accept()

    thread_id    = f"web-{id(browser_ws)}"
    bot_speaking = asyncio.Event()
    cancel_event = asyncio.Event()
    audio_queue  = asyncio.Queue()
    current_task = [None]

    sarvam = AsyncSarvamAI(api_subscription_key=SARVAM_API_KEY)

    # ── helpers ───────────────────────────────────────────────────────────────

    async def send_json(data: dict):
        try:
            await browser_ws.send_text(json.dumps(data))
        except Exception:
            pass

    async def send_bytes_ws(data: bytes):
        try:
            await browser_ws.send_bytes(data)
        except Exception:
            pass

    # ── STT loop (unchanged from original) ───────────────────────────────────

    async def stt_loop():
        bytes_per_chunk = int(SAMPLE_RATE * CHUNK_DURATION) * 2
        buffer = b""

        utt = {
            "confirmed_chunks": [],
            "current_segment":  "",
            "in_speech":        False,
        }
        end_tmr = None

        def full_text() -> str:
            parts = utt["confirmed_chunks"][:]
            if utt["current_segment"]:
                parts.append(utt["current_segment"])
            return " ".join(p.strip() for p in parts if p.strip())

        def reset_utterance():
            utt["confirmed_chunks"] = []
            utt["current_segment"]  = ""
            utt["in_speech"]        = False

        async with sarvam.speech_to_text_streaming.connect(
            model="saaras:v3",
            mode="transcribe",
            language_code="en-IN",
            high_vad_sensitivity=True,
            vad_signals=True,
        ) as stt_ws:

            async def send_audio_to_sarvam():
                nonlocal buffer
                while True:
                    chunk = await audio_queue.get()
                    if chunk is None:
                        break
                    rms = check_rms(chunk)
                    if rms > BARGE_IN_RMS:
                        cancel_event.set()
                        bot_speaking.clear()
                        if current_task[0] and not current_task[0].done():
                            current_task[0].cancel()
                        await send_json({"type": "barge_in"})
                    if bot_speaking.is_set():
                        continue
                    buffer += chunk
                    if len(buffer) >= bytes_per_chunk:
                        wav       = add_wav_header(buffer)
                        audio_b64 = base64.b64encode(wav).decode("utf-8")
                        await stt_ws.transcribe(
                            audio=audio_b64,
                            encoding="audio/wav",
                            sample_rate=SAMPLE_RATE,
                        )
                        buffer = b""

            async def receive_from_sarvam():
                nonlocal end_tmr

                async def finalize():
                    await asyncio.sleep(SILENCE_HOLD)
                    if utt["current_segment"]:
                        utt["confirmed_chunks"].append(utt["current_segment"])
                        utt["current_segment"] = ""
                    text = full_text()
                    if not text:
                        reset_utterance(); return
                    if len(text.split()) < MIN_INPUT_WORDS:
                        print(f"[TURN] SKIP — too short: '{text}'")
                        reset_utterance(); return
                    if only_hesitation(text):
                        print(f"[TURN] SKIP — hesitation only: '{text}'")
                        reset_utterance(); return
                    if not is_sentence_complete(text):
                        print(f"[TURN] SKIP — incomplete: '{text}'")
                        reset_utterance(); return
                    print(f"[TURN] ACCEPTED: '{text}'")
                    reset_utterance()          # clear BEFORE any await
                    await handle_transcript(text)

                async for message in stt_ws:
                    msg_type = getattr(message, "type", None)
                    data     = getattr(message, "data", None)
                    if msg_type == "events" and data:
                        signal_type = getattr(data, "signal_type", "") or ""
                        if signal_type == "START_SPEECH":
                            utt["in_speech"] = True
                            print("[VAD] ▶ START_SPEECH")
                            if end_tmr and not end_tmr.done():
                                end_tmr.cancel()
                        elif signal_type == "END_SPEECH":
                            utt["in_speech"] = False
                            print("[VAD] ■ END_SPEECH")
                            if utt["current_segment"]:
                                utt["confirmed_chunks"].append(utt["current_segment"])
                                utt["current_segment"] = ""
                            if end_tmr and not end_tmr.done():
                                end_tmr.cancel()
                            end_tmr = asyncio.create_task(finalize())
                    elif msg_type == "data" and data:
                        text = (getattr(data, "transcript", "") or "").strip()
                        if text:
                            utt["current_segment"] = text
                            acc = full_text()
                            print(f"[INTER] {acc}")
                            await send_json({"type": "interim", "text": acc})

            await asyncio.gather(send_audio_to_sarvam(), receive_from_sarvam())

    # ── LLM + TTS pipeline (sentence streaming) ───────────────────────────────

    async def process_transcript(transcript: str):
        sid          = str(uuid.uuid4())
        loop         = asyncio.get_event_loop()
        cancel_event.clear()

        await send_json({"type": "transcript", "text": transcript})
        await send_json({"type": "thinking"})

        llm_cancel = threading.Event()   # threading.Event — safe from executor
        sentence_q = _q.Queue()
        full_parts = []

        # Start LLM streaming in executor
        llm_future = loop.run_in_executor(
            None, stream_graph_sentences, transcript, thread_id, sentence_q, llm_cancel
        )

        await send_json({"type": "audio_start", "session_id": sid})
        bot_speaking.set()

        async def stream_tts_sentences():
            """
            Pull sentences from sentence_q as the LLM produces them.
            For each sentence, call ElevenLabs streaming TTS and forward
            PCM chunks to the browser immediately as they arrive.
            """
            while True:
                # Non-blocking poll — yield to event loop between checks
                try:
                    sentence = sentence_q.get_nowait()
                except _q.Empty:
                    await asyncio.sleep(0.01)
                    continue

                if sentence is None:
                    break   # LLM done

                if cancel_event.is_set():
                    # drain remaining sentences
                    while sentence_q.get() is not None:
                        pass
                    break

                full_parts.append(sentence)
                print(f"[TTS] synthesising: {sentence[:60]}…")

                
                # ElevenLabs true streaming — audio chunks arrive before full synthesis
                try:
                    audio_stream = await loop.run_in_executor(
                        None,
                        lambda s=sentence: eleven.text_to_speech.stream(
                            text=s,
                            voice_id=VOICE_ID,
                            model_id=TTS_MODEL,
                            output_format="pcm_24000",
                        )
                    )
                    for chunk in audio_stream:
                        if cancel_event.is_set():
                            break
                        if chunk:
                            await send_bytes_ws(chunk)
                            await asyncio.sleep(0)
                except Exception as e:
                    print(f"[TTS ERROR] {e}")

        try:
            await asyncio.gather(llm_future, stream_tts_sentences())
        except asyncio.CancelledError:
            llm_cancel.set()
            raise
        finally:
            llm_cancel.set()
            bot_speaking.clear()

        if not cancel_event.is_set():
            full_response = " ".join(full_parts)
            await send_json({"type": "response", "text": full_response})

        await send_json({"type": "audio_end", "session_id": sid})

    async def handle_transcript(transcript: str):
        if current_task[0] and not current_task[0].done():
            cancel_event.set()
            bot_speaking.clear()
            current_task[0].cancel()
            try:
                await current_task[0]
            except asyncio.CancelledError:
                pass
            await send_json({"type": "barge_in"})
        cancel_event.clear()
        current_task[0] = asyncio.create_task(process_transcript(transcript))

    # ── Browser message receiver ──────────────────────────────────────────────

    async def receive_from_browser():
        while True:
            msg = await browser_ws.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if msg.get("bytes"):
                await audio_queue.put(msg["bytes"])

    # ── Run everything ────────────────────────────────────────────────────────

    try:
        await asyncio.gather(receive_from_browser(), stt_loop())
    except WebSocketDisconnect:
        pass
    finally:
        if current_task[0] and not current_task[0].done():
            current_task[0].cancel()
        await audio_queue.put(None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)