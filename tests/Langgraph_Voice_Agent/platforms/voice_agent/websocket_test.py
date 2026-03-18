import asyncio
import sys
import os
import re
import json
import uuid
import base64
import struct
import numpy as np
import requests
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

MIN_INPUT_WORDS    = 1
LLM_DEBOUNCE       = 0.4
BARGE_IN_RMS       = 0.08 # Adjust this threshold based on testing
SAMPLE_RATE        = 16000
CHUNK_DURATION     = 0.1

# VAD settings for better turn detection
VAD_SPEECH_END_DELAY = 0.8  # Wait 800ms after speech ends before processing

# TTS STREAMING MODE - Choose your preferred method:
# "sentence" - Stream by sentences (good balance, no weird pauses)
# "native" - Use ElevenLabs native streaming (lowest latency)
TTS_STREAMING_MODE = "native"  # Change to "sentence" if you prefer

VOICE_ID  = "JBFqnCBsd6RMkjVDRZzb"  # George — multilingual
TTS_MODEL = "eleven_multilingual_v2"

eleven = ElevenLabs(api_key=ELEVENLABS_API_KEY)
app    = FastAPI()

with open(os.path.join(os.path.dirname(__file__), "voice_ui.html"), encoding="utf-8") as f:
    HTML = f.read()

@app.get("/")
async def index():
    return HTMLResponse(HTML)

def clean(text: str) -> str:
    text = re.sub(r'http\S+', '', text)
    text = re.sub(r'[•\*#]', '', text)
    return re.sub(r'\n', ' ', text).strip()

def split_into_sentences(text: str) -> list:
    """Split text into sentences while preserving natural breaks"""
    # Split on sentence boundaries but keep the punctuation
    sentences = re.split(r'([.!?]+\s+)', text)
    result = []
    for i in range(0, len(sentences)-1, 2):
        sentence = sentences[i] + (sentences[i+1] if i+1 < len(sentences) else '')
        if sentence.strip():
            result.append(sentence.strip())
    # Handle last sentence if no punctuation
    if sentences and sentences[-1].strip():
        # Check if we already added it
        if not result or result[-1] != sentences[-1].strip():
            result.append(sentences[-1].strip())
    return [s for s in result if s.strip()]

def add_wav_header(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    data_size = len(pcm_bytes)
    header = struct.pack("<4sI4s4sIHHIIHH4sI",
        b"RIFF", data_size + 36, b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate,
        sample_rate * 2, 2, 16,
        b"data", data_size
    )
    return header + pcm_bytes

def check_rms(audio_bytes: bytes) -> float:
    samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(samples**2))) if len(samples) > 0 else 0.0

def get_tts_audio(text: str) -> bytes:
    """Generate TTS audio (non-streaming, used for sentence mode)"""
    print(f"[TTS] Generating: {text[:60]}")
    audio_gen = eleven.text_to_speech.convert(
        text=clean(text),
        voice_id=VOICE_ID,
        model_id=TTS_MODEL,
        output_format="pcm_24000",
    )
    return b"".join(audio_gen)

def run_graph(transcript: str, thread_id: str) -> str:
    config = {"configurable": {"thread_id": thread_id}}
    full = ""
    for chunk, _ in graph.stream({"messages": transcript}, config, stream_mode="messages"):
        if hasattr(chunk, "content") and chunk.content:
            if type(chunk).__name__ in ("AIMessageChunk", "AIMessage"):
                full += chunk.content
    return full


@app.websocket("/ws")
async def websocket_endpoint(browser_ws: WebSocket):
    await browser_ws.accept()
    print("[WS] Browser connected")

    thread_id    = f"web-{id(browser_ws)}"
    bot_speaking = asyncio.Event()
    cancel_event = asyncio.Event()
    audio_queue  = asyncio.Queue()
    current_task = [None]
    
    # VAD state management
    last_transcript = {"text": "", "timestamp": 0}
    pending_process = [None]  # Stores the scheduled processing task
    speech_buffer = []  # Buffer to accumulate speech segments
    last_speech_time = [0]  # Track when last speech ended
    end_of_turn_timer = [None]  # Timer for detecting true end of turn

    sarvam = AsyncSarvamAI(api_subscription_key=SARVAM_API_KEY)

    async def send_json(data: dict):
        try: await browser_ws.send_text(json.dumps(data))
        except: pass

    async def send_bytes_ws(data: bytes):
        try: await browser_ws.send_bytes(data)
        except: pass

    async def stt_loop():
        bytes_per_chunk = int(SAMPLE_RATE * CHUNK_DURATION) * 2
        buffer = b""

        async with sarvam.speech_to_text_streaming.connect(
            model="saaras:v3",
            mode="transcribe",
            language_code="en-IN",
            high_vad_sensitivity=True,
            vad_signals=True,
        ) as stt_ws:
            print("[STT] Connected to Sarvam")

            async def send_audio_to_sarvam():
                nonlocal buffer
                while True:
                    chunk = await audio_queue.get()
                    if chunk is None:
                        break
                    
                    # Barge-in detection - CHECK THIS FIRST, EVEN IF NOT bot_speaking
                    rms = check_rms(chunk)
                    
                    if rms > BARGE_IN_RMS:
                        print(f"[BARGE-IN DETECTED] RMS={rms:.3f} - STOPPING EVERYTHING")
                        cancel_event.set()
                        bot_speaking.clear()
                        
                        # Cancel current playback task immediately
                        if current_task[0] and not current_task[0].done():
                            current_task[0].cancel()
                            print("[BARGE-IN] Cancelled current_task")
                        
                        # Cancel any pending processing
                        if pending_process[0] and not pending_process[0].done():
                            pending_process[0].cancel()
                            print("[BARGE-IN] Cancelled pending_process")
                        
                        await send_json({"type": "barge_in"})
                        # Don't continue here - process this audio for STT
                    
                    # Only skip STT processing if bot is speaking but no barge-in
                    if bot_speaking.is_set():
                        continue
                    
                    buffer += chunk
                    if len(buffer) >= bytes_per_chunk:
                        wav = add_wav_header(buffer, sample_rate=SAMPLE_RATE)
                        audio_b64 = base64.b64encode(wav).decode("utf-8")
                        await stt_ws.transcribe(audio=audio_b64, encoding="audio/wav", sample_rate=SAMPLE_RATE)
                        buffer = b""

            async def receive_from_sarvam():
                nonlocal last_transcript
                speech_ended = False
                end_speech_timer = [None]  # Stores the delayed processing task
                
                async def delayed_process():
                    """Wait for a timeout period, then process if no new speech starts"""
                    await asyncio.sleep(VAD_SPEECH_END_DELAY)
                    
                    # If we get here, no new speech started - process the accumulated text
                    if last_transcript["text"] and len(last_transcript["text"].split()) >= MIN_INPUT_WORDS:
                        print(f"[TURN_END] Processing accumulated: {last_transcript['text']}")
                        
                        # Cancel any existing pending process
                        if pending_process[0] and not pending_process[0].done():
                            pending_process[0].cancel()
                        
                        # Schedule processing with debounce
                        final_text = last_transcript["text"]
                        pending_process[0] = asyncio.create_task(
                            debounced_process(final_text)
                        )
                        
                        # Reset for next utterance
                        last_transcript["text"] = ""
                
                async for message in stt_ws:
                    msg_type = getattr(message, "type", None)
                    data     = getattr(message, "data", None)
                    
                    if msg_type == "events" and data:
                        signal_type = getattr(data, 'signal_type', '')
                        print(f"[STT EVENT] {signal_type}")
                        
                        if signal_type == "START_SPEECH":
                            # Cancel any pending end-of-turn processing
                            if end_speech_timer[0] and not end_speech_timer[0].done():
                                print(f"[START_SPEECH] Cancelling turn-end timer (user still speaking)")
                                end_speech_timer[0].cancel()
                                end_speech_timer[0] = None
                        
                        elif signal_type == "END_SPEECH":
                            # Don't process immediately - start a timer
                            # If another START_SPEECH comes before timer expires, we cancel it
                            print(f"[END_SPEECH] Starting {VAD_SPEECH_END_DELAY}s turn-end timer...")
                            
                            # Cancel previous timer if exists
                            if end_speech_timer[0] and not end_speech_timer[0].done():
                                end_speech_timer[0].cancel()
                            
                            # Start new timer
                            end_speech_timer[0] = asyncio.create_task(delayed_process())
                    
                    elif msg_type == "data" and data:
                        text = (getattr(data, "transcript", "") or "").strip()
                        if text:
                            # Update the accumulated transcript
                            last_transcript["text"] = text
                            last_transcript["timestamp"] = asyncio.get_event_loop().time()
                            print(f"[STT] {text}")
                            await send_json({"type": "interim", "text": text})

            await asyncio.gather(send_audio_to_sarvam(), receive_from_sarvam())

    async def debounced_process(transcript: str):
        """Wait for debounce period before processing"""
        await asyncio.sleep(LLM_DEBOUNCE)
        await handle_transcript(transcript)

    async def process_transcript_sentence_mode(transcript: str):
        """Process transcript with sentence-by-sentence streaming"""
        sid = str(uuid.uuid4())
        cancel_event.clear()

        print(f"[YOU] {transcript}")
        await send_json({"type": "transcript", "text": transcript})
        await send_json({"type": "thinking"})

        loop = asyncio.get_event_loop()

        print("[LLM] Running...")
        response_text = await loop.run_in_executor(None, run_graph, transcript, thread_id)
        print(f"[LLM] Done: {response_text[:60]}")

        if cancel_event.is_set():
            print("[LLM] Cancelled after completion")
            await send_json({"type": "audio_end", "session_id": sid})
            return

        await send_json({"type": "response", "text": response_text})

        # Split into sentences for streaming
        sentences = split_into_sentences(response_text)
        
        if not sentences:
            print("[TTS] No sentences to generate")
            return
        
        await send_json({"type": "audio_start", "session_id": sid})
        bot_speaking.set()
        
        print(f"[TTS] Streaming {len(sentences)} sentences...")
        
        for idx, sentence in enumerate(sentences):
            if cancel_event.is_set():
                print(f"[TTS] Cancelled at sentence {idx+1}/{len(sentences)}")
                break
            
            print(f"[TTS] Sentence {idx+1}/{len(sentences)}: {sentence[:40]}...")
            
            # Generate audio for this sentence
            audio_bytes = await loop.run_in_executor(None, get_tts_audio, sentence)
            
            if cancel_event.is_set():
                print(f"[TTS] Cancelled after generating sentence {idx+1}")
                break
            
            # Stream this sentence's audio immediately
            chunk_size = 1024
            bytes_sent = 0
            for i in range(0, len(audio_bytes), chunk_size):
                if cancel_event.is_set():
                    print(f"[AUDIO] INTERRUPTED at {bytes_sent}/{len(audio_bytes)} bytes")
                    break
                chunk = audio_bytes[i:i+chunk_size]
                await send_bytes_ws(chunk)
                bytes_sent += len(chunk)
                if i % (chunk_size * 5) == 0:
                    await asyncio.sleep(0)
            
            if cancel_event.is_set():
                break

        bot_speaking.clear()
        await send_json({"type": "audio_end", "session_id": sid})
        
        if cancel_event.is_set():
            print("[AUDIO] Ended due to cancellation")
        else:
            print("[AUDIO] Completed normally")

    async def process_transcript_native_streaming(transcript: str):
        """Process transcript with ElevenLabs native streaming"""
        sid = str(uuid.uuid4())
        cancel_event.clear()

        print(f"[YOU] {transcript}")
        await send_json({"type": "transcript", "text": transcript})
        await send_json({"type": "thinking"})

        loop = asyncio.get_event_loop()

        print("[LLM] Running...")
        response_text = await loop.run_in_executor(None, run_graph, transcript, thread_id)
        print(f"[LLM] Done: {response_text[:60]}")

        if cancel_event.is_set():
            print("[LLM] Cancelled after completion")
            await send_json({"type": "audio_end", "session_id": sid})
            return

        await send_json({"type": "response", "text": response_text})
        await send_json({"type": "audio_start", "session_id": sid})
        bot_speaking.set()

        print("[TTS] Starting native streaming...")
        
        try:
            # Use ElevenLabs native streaming API
            audio_stream = eleven.text_to_speech.convert(
                text=clean(response_text),
                voice_id=VOICE_ID,
                model_id=TTS_MODEL,
                output_format="pcm_24000",
            )
            
            bytes_sent = 0
            for chunk in audio_stream:
                if cancel_event.is_set():
                    print(f"[TTS] Cancelled during streaming at {bytes_sent} bytes")
                    break
                await send_bytes_ws(chunk)
                bytes_sent += len(chunk)
                await asyncio.sleep(0)  # Yield control for barge-in detection
            
            print(f"[TTS] Streamed {bytes_sent} bytes total")
                
        except Exception as e:
            print(f"[TTS ERROR] {e}")
            import traceback
            traceback.print_exc()
        
        bot_speaking.clear()
        await send_json({"type": "audio_end", "session_id": sid})
        
        if cancel_event.is_set():
            print("[AUDIO] Ended due to cancellation")
        else:
            print("[AUDIO] Completed normally")

    async def process_transcript(transcript: str):
        """Route to the appropriate processing mode"""
        if TTS_STREAMING_MODE == "sentence":
            await process_transcript_sentence_mode(transcript)
        elif TTS_STREAMING_MODE == "native":
            await process_transcript_native_streaming(transcript)
        else:
            print(f"[ERROR] Unknown TTS_STREAMING_MODE: {TTS_STREAMING_MODE}")
            await process_transcript_native_streaming(transcript)  # Default fallback

    async def handle_transcript(transcript: str):
        # Cancel existing task if running
        if current_task[0] and not current_task[0].done():
            print("[HANDLE] Cancelling existing task")
            cancel_event.set()
            bot_speaking.clear()
            current_task[0].cancel()
            try: 
                await current_task[0]
            except asyncio.CancelledError: 
                pass
            await send_json({"type": "barge_in"})
        
        # Clear cancel event and start new task
        cancel_event.clear()
        current_task[0] = asyncio.create_task(process_transcript(transcript))

    async def receive_from_browser():
        while True:
            msg = await browser_ws.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if msg.get("bytes"):
                await audio_queue.put(msg["bytes"])

    try:
        await asyncio.gather(receive_from_browser(), stt_loop())
    except WebSocketDisconnect:
        print("[WS] Browser disconnected")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[WS ERROR] {e}")
        import traceback; traceback.print_exc()
    finally:
        # Cleanup
        if current_task[0] and not current_task[0].done():
            current_task[0].cancel()
        if pending_process[0] and not pending_process[0].done():
            pending_process[0].cancel()
        await audio_queue.put(None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)