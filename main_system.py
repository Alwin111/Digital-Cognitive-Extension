import RPi.GPIO as GPIO
import time
import sqlite3
import sounddevice as sd
import numpy as np
import json
import threading
import queue
import requests
import subprocess
from vosk import Model, KaldiRecognizer
from difflib import SequenceMatcher
from datetime import datetime

# ======================================================
# RESAMPLE (no scipy)
# ======================================================
def resample_audio(audio_np, src_rate, dst_rate):
    if src_rate == dst_rate:
        return audio_np
    target_len = int(len(audio_np) * dst_rate / src_rate)
    indices    = np.linspace(0, len(audio_np) - 1, target_len)
    left       = np.floor(indices).astype(np.int32)
    right      = np.clip(left + 1, 0, len(audio_np) - 1)
    frac       = (indices - left).astype(np.float32)
    return (audio_np[left] * (1 - frac) + audio_np[right] * frac).astype(np.int16)

# ======================================================
# SPEAKER — MAX98357A via I2S (hw:0,0)
# ======================================================
def speak(text):
    """Speak text through MAX98357A using espeak-ng. Blocking."""
    clean = text.replace("*","").replace("•","").replace("-","").strip()
    if not clean:
        return
    try:
        wav = "/tmp/tts.wav"
        subprocess.run(
            ["espeak-ng", "-v", "en", "-s", "145", "-a", "200", "-w", wav, clean],
            timeout=10, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        subprocess.run(
            ["aplay", "-D", "hw:0,0", wav],
            timeout=15,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        print("[TTS] espeak-ng missing. Run: sudo apt install espeak-ng")
    except Exception as e:
        print("[TTS] Error:", e)

def speak_async(text):
    """Non-blocking speak."""
    threading.Thread(target=speak, args=(text,), daemon=True).start()

# ======================================================
# OLED — thread-safe via queue
# Only the main loop drains the queue and touches I2C.
# ======================================================
oled_queue = queue.Queue()

try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
    from PIL import Image, ImageDraw

    _serial = i2c(port=1, address=0x3C)
    _device = ssd1306(_serial)
    W, H    = _device.size   # typically 128x64

    def _render(text):
        try:
            image = Image.new("1", (W, H))
            draw  = ImageDraw.Draw(image)
            words = str(text).split()
            lines, line = [], ""
            for word in words:
                if len(line + word) <= 20:
                    line += word + " "
                else:
                    lines.append(line.strip())
                    line = word + " "
            lines.append(line.strip())
            y = 0
            for l in lines[:4]:
                draw.text((0, y), l, fill=255)
                y += 16
            _device.display(image)
        except OSError:
            pass   # I2C glitch — ignore

    print("OLED connected")

except Exception as _e:
    _device = None
    def _render(text):
        print("[OLED]:", text)
    print("OLED safe mode:", _e)

def _oled_loop():
    """Drain queue — call from main thread only."""
    try:
        while True:
            _render(oled_queue.get_nowait())
    except queue.Empty:
        pass

def show_text(text):
    """Thread-safe show. Any thread can call this."""
    oled_queue.put(str(text))

def oled_now(text):
    """Show immediately (call from main thread only)."""
    _render(str(text))

def slideshow(text, secs=3):
    """Show each bullet on OLED + speak it. Works from any thread."""
    # fix literal \n stored in DB
    text  = text.replace("\\n", "\n")
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    bullets = [l for l in lines if l.startswith(("*", "-", "•"))]
    slides  = bullets if bullets else lines

    if not slides:
        _render(text[:80])
        return

    for i, slide in enumerate(slides):
        clean = slide.lstrip("*-• ").strip()
        clean = clean.replace("**", "")
        header = f"{i+1}/{len(slides)}"
        _render(f"{header} {clean}")
        speak(clean)
        # wait secs but allow any button press to skip to next slide
        start = time.time()
        while time.time() - start < secs:
            if GPIO.input(BTN1) == 0 or GPIO.input(BTN2) == 0:
                break
            time.sleep(0.1)

# ======================================================
# GPIO
# ======================================================
GPIO.setwarnings(False)
BTN1, BTN2 = 17, 12
LED1, LED2  = 24, 23
GPIO.setmode(GPIO.BCM)
GPIO.setup(BTN1, GPIO.IN,  pull_up_down=GPIO.PUD_UP)
GPIO.setup(BTN2, GPIO.IN,  pull_up_down=GPIO.PUD_UP)
GPIO.setup(LED1, GPIO.OUT)
GPIO.setup(LED2, GPIO.OUT)

# ======================================================
# GEMINI API
# ======================================================
GEMINI_API_KEY = "AIzaSyCDBdR_wqj7wOY1IfdcotfpI4yeKuHAhpg"   # <-- paste your key
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1/models/"
    f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
)

def correct_and_summarize(raw_transcript):
    show_text("Analyzing...")
    print("Sending to Gemini...")

    prompt = (
        "You are processing a voice transcript from a low-quality offline speech recognizer "
        "on a Raspberry Pi. Words are often WRONG — homophones, garbled, nonsense phrases.\n\n"
        "ERROR EXAMPLES:\n"
        "- 'berries got' = 'there is gonna be'\n"
        "- 'eat the fish religions' = 'artificial intelligence'\n"
        "- 'diligent in a space' = 'AI in space'\n"
        "- 'meeting hall you have the main plus under' = 'meeting, main speaker'\n\n"
        "TASK:\n"
        "1. Use ALL context to figure out what was actually said.\n"
        "2. Extract ONLY important info: people, tasks, times, dates, decisions, reminders.\n"
        "3. Output ONLY bullet points (max 5, each max 12 words).\n"
        "4. Start every bullet with * symbol.\n"
        "5. If nothing important: output '* No important information found.'\n\n"
        f"Transcript:\n{raw_transcript}"
    )

    body = {"contents": [{"parts": [{"text": prompt}]}]}

    for attempt in range(3):
        try:
            r    = requests.post(GEMINI_URL,
                                 headers={"Content-Type": "application/json"},
                                 json=body, timeout=30)
            data = r.json()

            if r.status_code == 429:
                wait = 65
                try:
                    wait = int(data["error"]["details"][-1]["retryDelay"].replace("s","")) + 5
                except:
                    pass
                msg = f"API limit. Wait {wait}s"
                print(msg); show_text(msg)
                time.sleep(wait)
                continue

            if "candidates" not in data:
                print("Bad Gemini response:", json.dumps(data, indent=2))
                return raw_transcript[:300]

            summary = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            print("Summary:\n", summary)
            return summary

        except Exception as e:
            print("Gemini error:", e)
            time.sleep(5)

    return raw_transcript[:300]

# ======================================================
# DATABASE
# ======================================================
db_lock = threading.Lock()
conn    = sqlite3.connect("memory.db", check_same_thread=False)
cur     = conn.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS memory (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        raw_text  TEXT,
        summary   TEXT
    )
""")
conn.commit()

def save_memory(raw_text, summary):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    with db_lock:
        cur.execute("INSERT INTO memory (timestamp,raw_text,summary) VALUES(?,?,?)",
                    (ts, raw_text, summary))
        conn.commit()
    print(f"Saved [{ts}]")

def recall_memory(raw_query):
    """
    1. Send garbled query to Gemini to extract clean keywords
    2. Search all summaries using those keywords
    3. Return best matching memory
    """
    with db_lock:
        cur.execute("SELECT id, timestamp, summary FROM memory ORDER BY id DESC")
        rows = cur.fetchall()

    if not rows:
        return "* Memory is empty"

    # --- Step 1: Clean up the query with Gemini ---
    clean_query = raw_query  # fallback
    try:
        prompt = (
            "This is a garbled voice query from a speech recognizer with errors.\n"
            "Extract the KEY TOPIC the person is asking about — just 2-5 clean keywords.\n"
            "Output ONLY the keywords, nothing else.\n\n"
            f"Query: {raw_query}"
        )
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        r    = requests.post(GEMINI_URL,
                             headers={"Content-Type": "application/json"},
                             json=body, timeout=15)
        data = r.json()
        if "candidates" in data:
            clean_query = data["candidates"][0]["content"]["parts"][0]["text"].strip().lower()
            print(f"Gemini cleaned query: '{clean_query}'")
    except Exception as e:
        print("Query clean error:", e)

    # --- Step 2: Score every memory against clean keywords ---
    keywords = clean_query.lower().split()

    best_id, best_ts, best_summary, best_score = None, None, None, -1

    for row_id, ts, summary in rows:
        summary_lower = summary.lower()
        score = 0

        # keyword hit score — each keyword match counts
        for kw in keywords:
            if kw in summary_lower:
                score += 10

        # fuzzy similarity as tiebreaker
        score += SequenceMatcher(None, clean_query, summary_lower).ratio() * 5

        # recency bonus — newer entries score slightly higher on ties
        score += row_id * 0.01

        print(f"  [{row_id}] score={score:.2f} | {summary[:60]}")

        if score > best_score:
            best_score   = score
            best_id      = row_id
            best_ts      = ts
            best_summary = summary

    # if nothing matched well, return most recent
    if best_score < 1:
        row_id, ts, summary = rows[0]
        return f"[{ts}] (most recent)\n{summary}"

    return f"[{best_ts}]\n{best_summary}"

# ======================================================
# VOSK
# ======================================================
MODEL_PATH = "/home/spartan123/digial-cognitive-extension/vosk-model-small-en-us-0.15"
model      = Model(MODEL_PATH)
VOSK_RATE  = 16000

# ======================================================
# MIC FINDER
# ======================================================
def find_mic():
    """Return (index, rate, chans) of best real input device."""
    devices    = sd.query_devices()
    skip_words = ['pulse','default','dmix','sysdefault','lavrate',
                  'samplerate','speex','upmix','vdownmix']

    print("\n=== AUDIO DEVICES ===")
    for i, d in enumerate(devices):
        tag = " <<INPUT" if d['max_input_channels'] > 0 else ""
        print(f"  [{i}] in={d['max_input_channels']} '{d['name']}'{tag}")
    print("=====================\n")

    # pass 1: USB hw: device
    for i, d in enumerate(devices):
        n = d['name'].lower()
        if d['max_input_channels'] > 0 and 'hw:' in n and 'usb' in n:
            return i, int(d['default_samplerate']), d['max_input_channels']
    # pass 2: any real hw: device
    for i, d in enumerate(devices):
        n = d['name'].lower()
        if d['max_input_channels'] > 0 and 'hw:' in n and not any(s in n for s in skip_words):
            return i, int(d['default_samplerate']), d['max_input_channels']
    # pass 3: usb_mic alias
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0 and 'usb_mic' in d['name'].lower():
            return i, int(d['default_samplerate']), d['max_input_channels']
    # pass 4: any non-virtual input
    for i, d in enumerate(devices):
        n = d['name'].lower()
        if d['max_input_channels'] > 0 and not any(s in n for s in ['pulse','default','dmix']):
            return i, int(d['default_samplerate']), d['max_input_channels']

    print("WARNING: No real mic found")
    return None, 44100, 1

# ======================================================
# AUDIO HELPERS
# ======================================================
def frames_to_audio(frames, mic_rate, mic_chans):
    raw      = b"".join(frames)
    audio_np = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if mic_chans > 1:
        audio_np = audio_np.reshape(-1, mic_chans).mean(axis=1)
    return resample_audio(audio_np, mic_rate, VOSK_RATE)

def transcribe(resampled):
    rec   = KaldiRecognizer(model, VOSK_RATE)
    parts = []
    for i in range(0, len(resampled), 4000):
        chunk = resampled[i:i+4000].tobytes()
        if rec.AcceptWaveform(chunk):
            text = json.loads(rec.Result()).get("text","")
            if text:
                parts.append(text)
    last = json.loads(rec.FinalResult()).get("text","")
    if last:
        parts.append(last)
    return " ".join(parts).strip()

def open_mic_stream(callback):
    idx, rate, chans = find_mic()
    if idx is None:
        raise RuntimeError("No mic found")
    stream = sd.RawInputStream(
        samplerate=rate, blocksize=8192, dtype='int16',
        channels=chans, device=idx, callback=callback
    )
    stream.start()
    print(f"Stream open: device[{idx}] rate={rate} ch={chans}")
    return stream, rate, chans

# ======================================================
# RECORDING STATE
# ======================================================
recording_frames = []
recording_lock   = threading.Lock()
is_recording     = False
_rec_stream      = None
_rec_rate        = 44100
_rec_chans       = 1

def start_recording():
    global recording_frames, is_recording, _rec_stream, _rec_rate, _rec_chans

    with recording_lock:
        recording_frames = []
        is_recording     = True

    def cb(indata, fc, ti, status):
        with recording_lock:
            if is_recording:
                recording_frames.append(bytes(indata))

    try:
        _rec_stream, _rec_rate, _rec_chans = open_mic_stream(cb)
        GPIO.output(LED1, True)
        show_text("Recording...")       # <-- OLED shows "Recording..."
        speak_async("Recording")
        print("Recording started")
    except Exception as e:
        print("Mic error:", e)
        show_text("Mic Error!")
        speak_async("Microphone error")
        with recording_lock:
            is_recording = False

def stop_and_process():
    """Runs in background thread."""
    global is_recording, _rec_stream

    with recording_lock:
        is_recording = False
        frames       = list(recording_frames)

    # close stream — release device immediately
    if _rec_stream:
        try:
            _rec_stream.stop()
            _rec_stream.close()
        except:
            pass
        _rec_stream = None

    GPIO.output(LED1, False)
    show_text("Stop Recording")        # <-- OLED shows "Stop Recording"
    speak_async("Stopped")
    print(f"Stopped. {len(frames)} chunks.")
    time.sleep(0.8)

    if not frames:
        show_text("No Audio!")
        speak_async("No audio captured")
        return

    # --- transcribe ---
    show_text("Transcribing...")       # <-- OLED shows "Transcribing..."
    speak_async("Transcribing")
    print("Transcribing...")
    resampled      = frames_to_audio(frames, _rec_rate, _rec_chans)
    raw_transcript = transcribe(resampled)
    print(f"Raw: {raw_transcript[:300]}")

    if not raw_transcript:
        show_text("Nothing Heard")
        speak_async("Nothing was heard")
        return

    # --- Gemini ---
    show_text("Analyzing...")
    summary = correct_and_summarize(raw_transcript)
    save_memory(raw_transcript, summary)

    show_text("Saved!")
    speak_async("Memory saved")
    time.sleep(0.8)

    # --- slideshow of bullets ---
    slideshow(summary, secs=3)
    show_text("Ready")

def record_query(duration=5):
    """Open a fresh mic stream for recall query."""
    frames = []
    rate   = [44100]
    chans  = [1]

    def cb(indata, fc, ti, status):
        frames.append(bytes(indata))

    idx, r, c = find_mic()
    if idx is None:
        print("record_query: no mic found")
        _render("No Mic!")
        return ""

    print(f"record_query: opening device[{idx}] rate={r} ch={c}")
    try:
        stream = sd.RawInputStream(
            samplerate=r, blocksize=8192, dtype='int16',
            channels=c, device=idx, callback=cb
        )
        stream.start()
        rate[0], chans[0] = r, c
        _render("Listening...")
        print("Listening for query...")
        time.sleep(duration)
        stream.stop()
        stream.close()
        print(f"record_query: got {len(frames)} frames")
    except Exception as e:
        print(f"record_query error: {e}")
        _render("Mic Error!")
        return ""

    if not frames:
        print("record_query: no frames captured")
        return ""

    resampled = frames_to_audio(frames, rate[0], chans[0])
    result    = transcribe(resampled)
    print(f"record_query result: '{result}'")
    return result

# ======================================================
# STARTUP
# ======================================================
show_text("Starting up...")
speak_async("System ready")
print("System Ready")
print("BTN1 x1=start | BTN1 x2=stop+save | BTN2=recall")
show_text("Ready")

btn1_recording  = False
btn1_last_press = 0

# ======================================================
# MAIN LOOP
# ======================================================
while True:
    _oled_loop()   # drain OLED queue — I2C only from main thread

    # ---- MEMORY BUTTON (BTN1) ----
    if GPIO.input(BTN1) == 0:
        now = time.time()
        if now - btn1_last_press > 0.5:   # debounce
            btn1_last_press = now
            time.sleep(0.05)

            if not btn1_recording:
                # FIRST PRESS — start
                btn1_recording = True
                start_recording()
                print(">>> RECORDING ON")
            else:
                # SECOND PRESS — stop
                btn1_recording = False
                GPIO.output(LED1, False)
                show_text("Stop Recording")   # immediate OLED feedback
                oled_now("Stop Recording")
                print(">>> RECORDING OFF")
                threading.Thread(target=stop_and_process, daemon=True).start()

        while GPIO.input(BTN1) == 0:
            time.sleep(0.05)

    # ---- RECALL BUTTON (BTN2) ----
    if GPIO.input(BTN2) == 0:
        GPIO.output(LED2, True)
        _render("Recall Mode")
        speak_async("What do you want to recall?")
        print("\n[RECALL]")

        query = record_query(duration=5)
        print("Query:", query)

        result = recall_memory(query)
        print("Result:", result)
        # fix literal \n
        result_clean = result.replace("\\n", "\n").replace("**", "")
        # Show recall result
        _render(result_clean[:80])
        # Speak result
        speak_async(result_clean)
        # Keep displayed for 10 seconds or until button press
        start_wait = time.time()
        while time.time() - start_wait < 10:
            if GPIO.input(BTN1) == 0 or GPIO.input(BTN2) == 0:
                break
            time.sleep(0.1)

        while GPIO.input(BTN2) == 0:
            time.sleep(0.05)
        time.sleep(1)
        GPIO.output(LED2, False)
        _render("Ready")

    # blink LED1 while recording
    if btn1_recording:
        GPIO.output(LED1, int(time.time() * 2) % 2)

    time.sleep(0.1)
