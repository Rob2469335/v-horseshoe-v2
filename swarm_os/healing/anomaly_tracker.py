import collections
import time
import logging

log = logging.getLogger("anomaly_tracker")

MAX_TRACKED_SOURCES = 256  # Cap to prevent unbounded memory growth


class AnomalyTracker:
    def __init__(self, maxlen: int = 1000, alpha: float = 0.3, warning_threshold: float = 5.0):
        self.items = collections.deque(maxlen=maxlen)
        self.alpha = alpha  # Smoothing factor for EMA
        self.warning_threshold = warning_threshold
        # Using plain dicts instead of defaultdict to allow manual pruning
        self.ema_freq: dict[str, float] = {}
        self.last_time: dict[str, float] = {}

    def _prune_if_needed(self):
        """Prune oldest tracked sources to prevent unbounded memory growth."""
        if len(self.ema_freq) > MAX_TRACKED_SOURCES:
            # Remove oldest half (simple eviction — production would use LRU)
            to_remove = list(self.ema_freq.keys())[:MAX_TRACKED_SOURCES // 2]
            for key in to_remove:
                self.ema_freq.pop(key, None)
                self.last_time.pop(key, None)

    def record(self, source, level, reason, payload=None):
        now = time.time()

        # BUG FIX 1: Only compute EMA for real anomalies, not recursive forecast_warnings.
        # This prevents the recursive self.record() call from corrupting the EMA
        # with a near-zero dt_seconds (causing a massive artificial freq spike).
        if level != "forecast_warning":
            if source not in self.last_time:
                # BUG FIX 2: First-time source — initialize without computing frequency.
                # Without this guard, dt_seconds ≈ 0 spikes current_freq to 600+,
                # triggering an immediate false-positive warning on the very first anomaly.
                self.last_time[source] = now
                self.ema_freq[source] = 0.0
            else:
                dt_seconds = now - self.last_time[source]
                self.last_time[source] = now

                # Convert dt to frequency (anomalies per minute). Cap at 600 to avoid spikes.
                current_freq = min(60.0 / max(dt_seconds, 0.1), 600.0)

                # Update Exponential Moving Average (Exponential Smoothing)
                prev_ema = self.ema_freq.get(source, 0.0)
                self.ema_freq[source] = (self.alpha * current_freq) + ((1 - self.alpha) * prev_ema)

            # BUG FIX 3: Prune unbounded dict growth
            self._prune_if_needed()

        forecast = self.ema_freq.get(source, 0.0)

        item = {
            "source": source,
            "level": level,
            "reason": reason,
            "payload": payload or {},
            "timestamp": now,
            "forecast_freq": forecast
        }
        self.items.append(item)

        # Emit preemptive forecast warning if the predicted failure rate crosses threshold.
        # Guard: level != "forecast_warning" prevents infinite recursive loop.
        if forecast > self.warning_threshold and level != "forecast_warning":
            log.warning(
                f"PREDICTIVE FORECAST: '{source}' is degrading rapidly "
                f"(EMA: {forecast:.1f} anomalies/min). Emitting preemptive warning."
            )
            self.record(source, "forecast_warning", "EMA predicts impending failure", {"forecast_freq": forecast})

        return item

    def list(self):
        return list(self.items)
