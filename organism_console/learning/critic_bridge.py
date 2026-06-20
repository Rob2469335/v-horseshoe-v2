def score(self, event):
    score = 1.0

    if hasattr(event, "success") and not event.success:
        score -= 0.4

    return max(0.0, min(1.0, score))
