# organism_console/speech.py
import re
import subprocess
import threading
import logging

logger = logging.getLogger(__name__)

def clean_text_for_speech(text: str) -> str:
    if not text:
        return ""
    # 1. Remove planning and xml blocks
    text = re.sub(r"<plan>.*?(?:</plan>|$)", "", text, flags=re.DOTALL)
    text = re.sub(r"<strategic_intent>.*?(?:</strategic_intent>|$)", "", text, flags=re.DOTALL)
    text = re.sub(r"<tool_call[^>]*>.*?(?:</tool_call>|$)", "", text, flags=re.DOTALL)
    text = re.sub(r"<.*?>", "", text) # strip any other XML/HTML tags
    
    # 2. Strip markdown headers, bold, italics, code blocks
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL) # remove code blocks
    text = re.sub(r"`.*?`", "", text) # remove inline code
    text = re.sub(r"\*\*|__", "", text) # remove bold
    text = re.sub(r"\*|_", "", text) # remove italics
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE) # remove headers
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text) # replace link markdown [text](url) with just text
    
    # 3. Clean up whitespace and newlines
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def speak_async(text: str):
    cleaned = clean_text_for_speech(text)
    if not cleaned:
        return
        
    # Clean text to make it safe for PowerShell command line
    safe_text = cleaned.replace("'", "").replace('"', "").replace("\n", " ").strip()
    if not safe_text:
        return
        
    # PowerShell command to synthesize speech
    cmd = f"Add-Type -AssemblyName System.Speech; $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; $synth.Speak('{safe_text}')"
    
    def run():
        try:
            subprocess.run(["powershell", "-Command", cmd], capture_output=True, check=True)
        except Exception as e:
            logger.debug(f"Speech synthesis execution failed: {e}")
            
    threading.Thread(target=run, daemon=True).start()


def play_chime_async(type_name: str):
    import winsound
    def run():
        try:
            if type_name == "listening":
                winsound.Beep(880, 80)
                winsound.Beep(1200, 80)
                winsound.Beep(1500, 120)
            elif type_name == "success":
                winsound.Beep(1500, 100)
                winsound.Beep(2000, 150)
            elif type_name == "escalation":
                winsound.Beep(600, 150)
                winsound.Beep(450, 250)
            elif type_name == "error":
                winsound.Beep(300, 350)
        except Exception:
            pass
            
    threading.Thread(target=run, daemon=True).start()
