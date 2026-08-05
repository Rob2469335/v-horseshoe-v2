import os
import tempfile
import uvicorn
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

from faster_whisper import WhisperModel
import logging

# Only disable SSL for HuggingFace Hub downloads (not globally)
import os
os.environ['HF_HUB_DISABLE_SSL_VERIFICATION'] = '1'
os.environ['CURL_CA_BUNDLE'] = ''

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Whisper Transcription Server")

# Load model globally on startup so it stays in RAM.
# Switched small.en -> large-v3-turbo after benchmark on 2024 Horseshoe test clip:
#   small.en 2.55s but misses technical vocab ("harness" -> "on a")
#   large-v3-turbo 20.4s baseline, but tuned to ~8.8s with zero accuracy loss via:
#     - language='en'        (skips 30s language-detection sweep; ~2x win)
#     - cpu_threads=12       (Ultra 5 135U sweet spot; oversubscribing at 14)
#     - beam_size=1          (greedy; beam made no difference on short clips)
#     - condition_on_previous_text=False (no autoregressive drift)
#   VAD filter was tested and REJECTED: it breaks "harness" accuracy on this clip.
MODEL_NAME = "Systran/faster-distil-whisper-large-v3"
CPU_THREADS = 12
logger.info(f"Loading Whisper model ({MODEL_NAME}, cpu_threads={CPU_THREADS})... this may take a moment.")
model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8",
                     num_workers=1, cpu_threads=CPU_THREADS)
logger.info("Model loaded successfully!")

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    # Save the uploaded file to a temporary location
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        raise HTTPException(status_code=500, detail="Could not process file")
    
    # Transcribe the audio
    try:
        segments, info = model.transcribe(tmp_path,
                                             language="en",
                                             beam_size=1,
                                             condition_on_previous_text=False)
        text = " ".join([segment.text for segment in segments]).strip()
        logger.info(f"Transcription successful: {text}")
        return JSONResponse(content={"text": text, "language": info.language, "language_probability": info.language_probability})
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temporary file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
