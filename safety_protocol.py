import litellm
import logging
import asyncio

log = logging.getLogger(__name__)

def apply_safety_protocol():
    """
    Comprehensive safety protocol to address agent failures and dropped connections:
    1. litellm.Timeout (180s): Caused by aiohttp socket read timeouts, typically with local llama.cpp models.
    2. RateLimitError (429): Caused by OpenRouter upstream rate limits.
    """
    
    # 1. Prevent 180s hangs by setting a strict global request timeout.
    # If a model doesn't respond in 45 seconds, it's likely stuck. Fail fast to allow fallbacks.
    litellm.request_timeout = 45.0 

    # 2. Configure automatic retries for transient errors and rate limits (429s).
    # This will automatically retry with exponential backoff before failing the step.
    litellm.num_retries = 3
    
    # 3. Setup failure callbacks to monitor dropped connections and rate limits in real-time
    def _failure_callback(kwargs, completion_response, start_time, end_time):
        exception = kwargs.get("exception")
        if exception:
            if isinstance(exception, litellm.Timeout):
                log.error(f"[SAFETY PROTOCOL] Model timeout detected for {kwargs.get('model')}. Enforcing fast failover.")
            elif isinstance(exception, litellm.RateLimitError):
                log.warning(f"[SAFETY PROTOCOL] Rate limit hit for {kwargs.get('model')}. LiteLLM will retry automatically.")
            else:
                log.error(f"[SAFETY PROTOCOL] Unexpected error: {str(exception)}")

    litellm.failure_callback = [_failure_callback]

    # 4. Optional: Circuit breaker configurations to avoid spamming rate-limited APIs
    litellm.module_level_retry = True
    
    log.info("Agent safety protocol applied: request_timeout=45s, num_retries=3, failure_callbacks attached.")

if __name__ == "__main__":
    apply_safety_protocol()
    print("Safety protocol configured successfully. To use, import and call `apply_safety_protocol()` at the application entry point.")
