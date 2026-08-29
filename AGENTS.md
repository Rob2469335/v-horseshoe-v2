# QLoRA XPU Training: Lessons Learned

## Infrastructure: Intel XPU iGPU Memory Locks
- **The Problem:** `torch.xpu.empty_cache()` does not reliably release shared VRAM on Intel iGPUs. Loading and unloading multiple models sequentially within the same long-running Python process will eventually trigger an Out-Of-Memory error or a silent hang.
- **The Fix:** Architecture your pipeline so that each major stage (extraction, formatting, training, generation, judging) runs in its own isolated Python subprocess. Memory is guaranteed to be reclaimed when the process fully exits.
- **TDR (Timeout Detection and Recovery):** Windows watchdog will force-reset the display driver if the GPU is unresponsive for >2s, which looks like a silent hang during heavy compute. Fix this by setting `TdrDelay` to `20` in the registry and rebooting.

## Architecture: Path 1 vs Path 2 (Reasoning vs Formatting)
- We tested two paths for making a LoRA adopt a specific CoT `<think>` structure:
  - **Path 1 (Full CoT Traces):** Train the adapter on the base model's genuine reasoning traces.
  - **Path 2 (System Prompt Formatting):** Train only on the final output, using a system prompt to suppress or format `<think>`.
- **Finding:** Path 2 successfully enforces the format, but the reasoning depth severely regresses. The model outputs generic boilerplate instead of deep, codebase-specific insights. To preserve reasoning capability, the adapter *must* be trained on full, truthful reasoning traces (Path 1).

## Training Data Extraction: Token Limits and Truncation
- When extracting real CoT traces from a base model, the model will often think for a very long time (1000+ tokens). 
- **The Risk:** If you enforce a strict `max_new_tokens` (e.g., 800) during dataset generation, the base model will be truncated mid-thought. If you wrap this truncated thought in a synthetic `</think>` tag and train on it, the adapter will learn to spontaneously cut off its own reasoning and jump straight to the answer.
- **The Fix:** Ensure `max_new_tokens` is generous (1500-2048) during trace extraction, and strictly enforce a `StoppingCriteria` on `</think>` so the generation cleanly halts precisely when the model finishes its natural thought process.
