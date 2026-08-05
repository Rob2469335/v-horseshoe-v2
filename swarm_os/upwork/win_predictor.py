from swarm_os.upwork.learning_engine_qdrant import search_similar


def compute_win_probability(vector, job_text: str, bid_amount: float):

    results = search_similar(vector, limit=25)

    if not results:
        return {
            "win_probability": 0.5,
            "confidence": "LOW",
            "recommendation": "BASELINE BID",
            "mode": "v2-cold-start"
        }

    # v2: cluster-aware weighting (simple proxy: payload grouping)
    clusters = {}
    total_weight = 0
    win_score = 0
    bids = []

    for r in results:
        p = r.payload or {}

        outcome = p.get("outcome")
        bid = p.get("bid", bid_amount)
        client_type = p.get("client_type", "unknown")

        weight = max(r.score, 0.1)

        clusters.setdefault(client_type, {"wins": 0, "total": 0})

        if outcome == "won":
            win_score += weight
            clusters[client_type]["wins"] += 1

        clusters[client_type]["total"] += 1

        total_weight += weight
        bids.append(bid)

    base = win_score / total_weight if total_weight else 0.5

    avg_bid = sum(bids) / len(bids)

    # bid normalization
    if bid_amount < avg_bid * 0.85:
        adj = 0.08
    elif bid_amount > avg_bid * 1.15:
        adj = -0.08
    else:
        adj = 0.0

    final = max(0.05, min(0.95, base + adj))

    if final > 0.7:
        rec = "AGGRESSIVE BID"
        conf = "HIGH"
    elif final > 0.45:
        rec = "NORMAL BID"
        conf = "MEDIUM"
    else:
        rec = "REWRITE OR SKIP"
        conf = "LOW"

    return {
        "win_probability": round(final, 2),
        "confidence": conf,
        "recommendation": rec,
        "avg_similar_bid": round(avg_bid, 2),
        "client_clusters": clusters,
        "mode": "v2"
    }
