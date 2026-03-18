"""
test_sarvam_stt.py  (v5 — cross-chunk accumulation)
─────────────────────────────────────────────────────
Key fix: accumulates transcript chunks across an entire
START_SPEECH → (multiple partial END/START) → final END_SPEECH
window so fragmented sentences like
  "I wanna know..." + "...weather..." + "...I can get a room"
are joined into one full utterance before going to the LLM.

pip install sarvamai sounddevice numpy python-dotenv
python test_sarvam_stt.py
"""

import asyncio
import base64
import os
import struct
import sys
import time
from datetime import datetime

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from sarvamai import AsyncSarvamAI

load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
SARVAM_API_KEY        = os.getenv("SARVAM_API_KEY", "")
SAMPLE_RATE           = 16000
CHUNK_DURATION        = 0.5

LANGUAGE              = "en-IN"
HIGH_VAD_SENS         = True

# How long silence must hold after the LAST END_SPEECH before we finalise.
# Raise this if sentences still get cut. Lower it for snappier response.
SILENCE_HOLD          = 1.2         # seconds

MIN_INPUT_WORDS       = 2
LLM_DEBOUNCE          = 0.3         # small — most wait is already in SILENCE_HOLD
HESITATION_WORDS      = {"um", "uh", "hmm"}

RMS_LOG_THRESHOLD     = 0.02
# ─────────────────────────────────────────────


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

RESET="\033[0m"; CYAN="\033[96m"; GREEN="\033[92m"
YELLOW="\033[93m"; RED="\033[91m"; BLUE="\033[94m"; MAG="\033[95m"

def log(tag, msg, c=""):
    print(f"{c}[{ts()}] [{tag:<5}] {msg}{RESET if c else ''}", flush=True)

def add_wav_header(pcm, sr=16000):
    n = len(pcm)
    return struct.pack("<4sI4s4sIHHIIHH4sI",
        b"RIFF",n+36,b"WAVE",b"fmt ",16,1,1,sr,sr*2,2,16,b"data",n)+pcm

def calc_rms(pcm):
    s = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)/32768.0
    return float(np.sqrt(np.mean(s**2))) if len(s) else 0.0

def only_hesitation(text):
    return all(w in HESITATION_WORDS for w in text.lower().split())


class Stats:
    chunks_sent=0; turns=0; turns_ok=0; turns_skip=0
    latencies=[]; _end_t=None
    def end_speech(self): self._end_t = time.perf_counter()
    def final(self, ok):
        self.turns+=1
        if ok: self.turns_ok+=1
        else:  self.turns_skip+=1
        ms=None
        if self._end_t:
            ms=(time.perf_counter()-self._end_t)*1000
            self.latencies.append(ms)
            self._end_t=None
        return ms
    def show(self):
        print("\n"+"="*52+"\n  SESSION SUMMARY\n"+"="*52)
        print(f"  Chunks sent  : {self.chunks_sent}")
        print(f"  Turns        : {self.turns}  (ok={self.turns_ok} skipped={self.turns_skip})")
        if self.latencies:
            print(f"  END→final    : avg={sum(self.latencies)/len(self.latencies):.0f}ms "
                  f"min={min(self.latencies):.0f}ms max={max(self.latencies):.0f}ms")
        print("="*52+"\n")

stats = Stats()


async def main():
    if not SARVAM_API_KEY:
        sys.exit("ERROR: SARVAM_API_KEY not set")

    print("\n"+"="*52)
    print("  SARVAM STT TESTER  v5 — accumulation mode")
    print(f"  Language={LANGUAGE}  chunk={CHUNK_DURATION}s")
    print(f"  Silence hold={SILENCE_HOLD}s  debounce={LLM_DEBOUNCE}s")
    print("  Ctrl+C to stop.\n"+"="*52+"\n")

    sarvam   = AsyncSarvamAI(api_subscription_key=SARVAM_API_KEY)
    ev_loop  = asyncio.get_event_loop()
    chunk_sz = int(SAMPLE_RATE * CHUNK_DURATION)

    # ── Utterance state ───────────────────────────────────────────
    # We track a list of confirmed segment transcripts + the current
    # in-progress segment transcript.
    #
    #  confirmed_chunks : list[str]  — segments locked in after each END_SPEECH
    #  current_segment  : str        — latest interim for the live segment
    #  in_speech        : bool       — currently between START and END
    #
    utt = {
        "confirmed_chunks": [],   # locked segments so far
        "current_segment":  "",   # interim for the active segment
        "in_speech":        False,
    }
    end_tmr: asyncio.Task | None = None

    def full_text() -> str:
        """Join all confirmed chunks + current live segment."""
        parts = utt["confirmed_chunks"][:]
        if utt["current_segment"]:
            parts.append(utt["current_segment"])
        return " ".join(p.strip() for p in parts if p.strip())

    def reset_utterance():
        utt["confirmed_chunks"] = []
        utt["current_segment"]  = ""
        utt["in_speech"]        = False

    async def sarvam_connect():
        async with sarvam.speech_to_text_streaming.connect(
            model="saaras:v3",
            mode="transcribe",
            language_code=LANGUAGE,
            high_vad_sensitivity=HIGH_VAD_SENS,
            vad_signals=True,
        ) as stt_ws:

            log("SYS", "Connected to Sarvam  |  opening mic...", GREEN)

            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=chunk_sz,
            ) as stream:

                log("SYS", "Mic open — SPEAK NOW!", GREEN)

                # ── SENDER ────────────────────────────────────────
                async def sender():
                    while True:
                        audio, _ = await ev_loop.run_in_executor(
                            None, stream.read, chunk_sz)
                        pcm = audio.tobytes()
                        r   = calc_rms(pcm)
                        if r > RMS_LOG_THRESHOLD:
                            log("MIC", f"RMS={r:.4f}", BLUE)
                        wav = add_wav_header(pcm)
                        b64 = base64.b64encode(wav).decode()
                        await stt_ws.transcribe(
                            audio=b64, encoding="audio/wav",
                            sample_rate=SAMPLE_RATE)
                        stats.chunks_sent += 1

                # ── RECEIVER ──────────────────────────────────────
                async def receiver():
                    nonlocal end_tmr

                    async def finalize():
                        """
                        Called SILENCE_HOLD seconds after the last END_SPEECH.
                        By then any barge-back-in would have cancelled us already,
                        so if we reach here the user has genuinely stopped.
                        """
                        await asyncio.sleep(SILENCE_HOLD)

                        # lock in the last segment if still pending
                        if utt["current_segment"]:
                            utt["confirmed_chunks"].append(utt["current_segment"])
                            utt["current_segment"] = ""

                        text = full_text()
                        log("TURN", f"Evaluating full turn: '{text}'", YELLOW)

                        def skip(reason):
                            log("TURN", f"SKIP — {reason}", RED)
                            stats.final(False)
                            reset_utterance()

                        if not text:
                            return skip("empty")
                        if len(text.split()) < MIN_INPUT_WORDS:
                            return skip(f"too short ({len(text.split())} word(s))")
                        if only_hesitation(text):
                            return skip("hesitation only")

                        log("TURN", f"ACCEPTED  debouncing {LLM_DEBOUNCE}s...", GREEN)
                        await asyncio.sleep(LLM_DEBOUNCE)

                        ms = stats.final(True)
                        log("FINAL",
                            f'→ LLM: "{text}"  '
                            f'[{len(text.split())} words | '
                            f'END→here: {f"{ms:.0f}ms" if ms else "n/a"}]',
                            GREEN)
                        reset_utterance()

                    async for msg in stt_ws:
                        mtype = getattr(msg, "type", None)
                        data  = getattr(msg, "data", None)

                        # ── VAD events ────────────────────────────
                        if mtype == "events" and data:
                            sig = getattr(data, "signal_type", "") or ""

                            if sig == "START_SPEECH":
                                utt["in_speech"] = True
                                log("VAD", "▶  START_SPEECH", CYAN)

                                # cancel the silence timer — user is still talking
                                if end_tmr and not end_tmr.done():
                                    end_tmr.cancel()
                                    log("VAD", "   (silence timer cancelled — continuing utterance)", CYAN)
                                    # keep confirmed_chunks intact — don't reset!

                            elif sig == "END_SPEECH":
                                utt["in_speech"] = False
                                log("VAD", "■  END_SPEECH", CYAN)

                                # lock the current segment into confirmed list
                                if utt["current_segment"]:
                                    utt["confirmed_chunks"].append(utt["current_segment"])
                                    utt["current_segment"] = ""
                                    log("VAD",
                                        f"   locked segment → chunks so far: "
                                        f"{utt['confirmed_chunks']}",
                                        CYAN)

                                stats.end_speech()

                                # restart silence timer
                                if end_tmr and not end_tmr.done():
                                    end_tmr.cancel()
                                end_tmr = asyncio.create_task(finalize())

                            else:
                                log("VAD", f"signal={sig!r}", CYAN)

                        # ── Transcript data ───────────────────────
                        elif mtype == "data" and data:
                            text = (getattr(data, "transcript", "") or "").strip()
                            conf = getattr(data, "confidence", None)
                            if text:
                                # always update the current (in-progress) segment
                                utt["current_segment"] = text
                                extras = f"  conf={conf:.2f}" if conf is not None else ""

                                # show accumulated + current for context
                                acc = full_text()
                                log("INTER",
                                    f"segment='{text}'{extras}  "
                                    f"| accumulated='{acc}'",
                                    YELLOW)

                        else:
                            if mtype is not None:
                                log("RAW", f"type={mtype!r} data={str(data)[:100]}", MAG)

                send_task = asyncio.create_task(sender())
                recv_task = asyncio.create_task(receiver())
                done, pending = await asyncio.wait(
                    [send_task, recv_task],
                    return_when=asyncio.FIRST_EXCEPTION)
                for t in pending: t.cancel()
                for t in done:
                    exc = t.exception()
                    if exc: log("ERR", str(exc), RED)

    await sarvam_connect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        print()
        stats.show()
        log("SYS", "Stopped.")