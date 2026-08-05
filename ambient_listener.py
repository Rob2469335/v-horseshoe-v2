import time
import numpy as np
import sounddevice as sd
import collections
import requests
import io
import wave
import torch
import torch.nn.functional as F
import torchaudio
import os
import sys
import ssl
import certifi
import pathlib
import shutil
import msvcrt # For Windows-specific non-blocking keyboard input

from openwakeword.model import Model
from openwakeword.utils import download_models
from speechbrain.inference.speaker import SpeakerRecognition
from speechbrain.utils.fetching import LocalStrategy

from voice_routing import (
    polish_transcript,
    type_text_via_sendkeys,
    # DICTATION_COUNTDOWN_SECONDS, # This will be replaced by CONFIRMATION_TIMEOUT
)

# --- CONFIGURATION ---
WAKE_WORD = os.environ.get("AMBIENT_WAKE_WORD", "hey_mycroft")
VOICE_REF_FILE = os.environ.get("AMBIENT_VOICE_REF_FILE", "my_voice.wav")
WHISPER_URL = os.environ.get("AMBIENT_WHISPER_URL", "http://127.0.0.1:8001/transcribe")
SAMPLE_RATE = 16000
CHUNK_SIZE = 1280  # 80ms at 16000Hz
VAD_SILENCE_TIMEOUT_FRAMES = 15  # ~1.2 seconds of silence

# --- NEW CONFIGURATION FOR CONFIRMATION GATE ---
CONFIRMATION_TIMEOUT = int(os.environ.get("AMBIENT_CONFIRMATION_TIMEOUT", 5)) # Seconds to wait for user confirmation

# Windows symlink fix - scoped to just the pathlib operations that need it
if os.name == 'nt':
    def _copy_instead_of_symlink(self, target, target_is_directory=False):
        try:
            if target.is_dir():
                shutil.copytree(target, self, dirs_exist_ok=True)
            else:
                shutil.copy(target, self)
        except OSError as e:
            if not isinstance(e, FileExistsError):
                print(f"Warning: Failed to copy {target} to {self}: {e}")
    pathlib.Path.symlink_to = _copy_instead_of_symlink

print(f"[{time.strftime('%X')}] Initializing Ambient Voice Architecture...")

if not os.path.exists(VOICE_REF_FILE):
    print(f"ERROR: {VOICE_REF_FILE} not found!")
    print("Please run `python record_voice.py` to create your biometric voice fingerprint first.")
    sys.exit(1)

# 1. Initialize Wake Word Model
print(f"[{time.strftime('%X')}] Checking for OpenWakeWord models...")
download_models()

print(f"[{time.strftime('%X')}] Loading OpenWakeWord model ('{WAKE_WORD}')...")
oww_model = Model(wakeword_models=[WAKE_WORD], inference_framework="onnx")

# 2. Initialize Voice Biometric Model
print(f"[{time.strftime('%X')}] Loading SpeechBrain Speaker Recognition model (Voice Biometrics)...")

verifier = SpeakerRecognition.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb",
    savedir="pretrained_models/spkrec-ecapa-voxceleb",
    local_strategy=LocalStrategy.COPY,
    run_opts={"device": "cpu"}
)

# Load the averaged enrollment embedding created by record_voice_embeddings.py.
VOICE_EMBEDDING_FILE = "my_voice_embedding.pt"

if not os.path.exists(VOICE_EMBEDDING_FILE):
    print(f"ERROR: {VOICE_EMBEDDING_FILE} not found!")
    print("Please run `python record_voice_embeddings.py` to enroll your voice first.")
    sys.exit(1)

print(f"[{time.strftime('%X')}] Loading enrolled voice embedding from {VOICE_EMBEDDING_FILE}...")
reference_embedding = torch.load(VOICE_EMBEDDING_FILE, map_location="cpu", weights_only=True)
if reference_embedding.ndim == 2:
    reference_embedding = reference_embedding.unsqueeze(0)
reference_embedding = F.normalize(reference_embedding.float(), p=2, dim=-1)

BIOMETRIC_THRESHOLD = float(os.environ.get("AMBIENT_BIOMETRIC_THRESHOLD", 0.10)) # Recalibrated from real data: user scored 0.21, TV scored 0.018 after fresh enrollment

ring_buffer = collections.deque(maxlen=100)
recording_buffer = []

# SSL context for secure requests
ssl_context = ssl.create_default_context(cafile=certifi.where())

def verify_speaker(audio_bytes):
    """Compare live audio against the averaged enrolled voice embedding."""
    audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    live_waveform = torch.from_numpy(audio_np).unsqueeze(0)

    try:
        with torch.no_grad():
            live_embedding = verifier.encode_batch(live_waveform)
            live_embedding = F.normalize(live_embedding.float(), p=2, dim=-1)
            score = F.cosine_similarity(
                live_embedding,
                reference_embedding,
                dim=-1
            ).item()

        return score > BIOMETRIC_THRESHOLD, score
    except (RuntimeError, ValueError, TypeError) as e:
        print(f"Verification error: {e}")
        return False, 0.0
def create_wav_buffer(audio_bytes):
    """Wraps raw PCM bytes into an in-memory WAV file to send to Whisper."""
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_bytes)
    wav_io.seek(0)
    return wav_io

def post_with_retry(url, files, max_retries=3, timeout=30):
    """POST with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            response = requests.post(url, files=files, timeout=timeout, verify=ssl_context)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise
            wait_time = 2 ** attempt
            print(f"Request failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_time}s...")
            time.sleep(wait_time)
    return None

# --- NEW FUNCTION FOR CONFIRMATION GATE ---
def get_user_confirmation(text_to_type):
    """
    Waits for user confirmation via keyboard input before typing.
    Returns True if confirmed, False if cancelled or timed out.
    """
    print(f"⏳ Press ENTER within {CONFIRMATION_TIMEOUT}s to type this, any other key to cancel:")
    start_time = time.time()
    confirmed = False
    
    while time.time() - start_time < CONFIRMATION_TIMEOUT:
        if msvcrt.kbhit():
            key = msvcrt.getch()
            if key == b'\r':  # Enter key
                confirmed = True
                break
            else:  # Any other key
                confirmed = False
                break
        time.sleep(0.05) # Small sleep to prevent busy-waiting

    if confirmed:
        print("✅ Confirmed.")
        return True
    else:
        if time.time() - start_time >= CONFIRMATION_TIMEOUT:
            print("⏱️ Timed out — not typed.")
        else:
            print("❌ Cancelled by user.")
        return False

print(f"\n[{time.strftime('%X')}] >>> AMBIENT LISTENER ACTIVE <<<")
print(f"Listening for '{WAKE_WORD}'...")

try:
    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=CHUNK_SIZE,
                           dtype='int16', channels=1) as stream:
        
        while True:
            try:
                chunk, overflowed = stream.read(CHUNK_SIZE)
                if overflowed:
                    print(f"[{time.strftime('%X')}] Warning: Audio buffer overflow")
                chunk_bytes = bytes(chunk)
                
                # Feed to openwakeword
                chunk_np = np.frombuffer(chunk_bytes, dtype=np.int16)
                prediction = oww_model.predict(chunk_np)
                
                score = list(prediction.values())[0]

                
                
                if score > 0.5:
                    print(f"\n[{time.strftime('%X')}] 🔔 Wake word detected! (score: {score:.2f})")
                    print("Checking Voice Biometrics...")
                    
                    # Record until we capture actual speech
                    verification_chunks = []
                    speech_chunks_collected = 0
                    max_wait_chunks = 100
                    waited_chunks = 0
                    SPEECH_RMS_THRESHOLD = 500

                    while speech_chunks_collected < 50 and waited_chunks < max_wait_chunks:
                        vc, _ = stream.read(CHUNK_SIZE)
                        vc_bytes = bytes(vc)
                        chunk_np = np.frombuffer(vc_bytes, dtype=np.int16).astype(np.float32)
                        rms = np.sqrt(np.mean(chunk_np**2))

                        verification_chunks.append(vc_bytes)
                        waited_chunks += 1

                        if rms > SPEECH_RMS_THRESHOLD:
                            speech_chunks_collected += 1
                    
                    verify_bytes = b''.join(verification_chunks)
                    
                    is_match, similarity = verify_speaker(verify_bytes)
                    print(f"Biometric Match: {is_match} (Score: {similarity:.2f})")
                    
                    if not is_match:
                        print("❌ Voice rejected. Ignoring command.")
                        print(f"\nListening for '{WAKE_WORD}'...")
                        continue
                    
                    print("✅ Voice Verified! Recording command...")
                    recording_buffer = list(verification_chunks)
                    
                    # Continuous biometric verification during recording
                    chunks_since_last_check = 0
                    consecutive_non_user_frames = 0
                    MIN_SPEECH_CHUNKS = 50  # Require minimum speech before biometric cutoff
                    
                    while len(recording_buffer) < 250:
                        rc, _ = stream.read(CHUNK_SIZE)
                        rc_bytes = bytes(rc)
                        recording_buffer.append(rc_bytes)
                        chunks_since_last_check += 1
                        
                        if chunks_since_last_check >= 10 and len(recording_buffer) > MIN_SPEECH_CHUNKS:
                            chunks_since_last_check = 0
                            recent_audio = b''.join(recording_buffer[-15:])
                            is_still_user, _ = verify_speaker(recent_audio)
                            
                            if not is_still_user:
                                consecutive_non_user_frames += 1
                            else:
                                consecutive_non_user_frames = 0
                                
                            if consecutive_non_user_frames >= 2:
                                recording_buffer = recording_buffer[:-30]
                                break
                    
                    print(f"[{time.strftime('%X')}] Recording complete. Transcribing...")
                    
                    full_audio = b''.join(recording_buffer)
                    wav_file = create_wav_buffer(full_audio)
                    
                    start_time = time.time()
                    try:
                        response = post_with_retry(WHISPER_URL, files={'file': ('command.wav', wav_file)})
                        elapsed = time.time() - start_time
                        result = response.json()
                        transcript = result.get('text', '').strip()
                        print(f"Whisper Transcript ({elapsed:.2f}s): {transcript}")
                        
                        if not transcript:
                            print(f"\nListening for '{WAKE_WORD}'...")
                            continue
                        
                        print(f"[{time.strftime('%X')}] [MODE: DICTATION] Processing command...")
                        polished = polish_transcript(transcript)
                        print(f"  Polished text: {polished}")

                        # --- INTRODUCING CONFIRMATION GATE ---
                        if get_user_confirmation(polished):
                            print("  Click your target window now! Typing in 2 seconds...")
                            time.sleep(2)
                            try:
                                print("  [DEBUG] SendKeys call starting...")
                                type_text_via_sendkeys(polished)
                                print(f"  Typed {len(polished)} chars into focused window.")
                            except Exception as e:
                                print(f"  SendKeys failed: {e}")
                        # Else: handled by get_user_confirmation output (timeout/cancel)
                                
                    except requests.exceptions.RequestException as e:
                        print(f"Failed to reach Whisper server: {e}")
                    
                    print(f"\nListening for '{WAKE_WORD}'...")
                    
            except sd.PortAudioError as e:
                print(f"[{time.strftime('%X')}] Audio stream error: {e}")
                time.sleep(1)
               
except KeyboardInterrupt:
    print("\nShutting down...")
except Exception as e:
    print(f"Fatal error: {e}")
    import traceback
    traceback.print_exc()



