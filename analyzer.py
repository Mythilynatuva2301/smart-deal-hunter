"""Price statistics and a rule-based fallback recommendation (no LLM needed)."""


def _slope_pct_per_check(vals, window=20):
    """Least-squares slope over the last `window` points, as % of the mean per check."""
    ys = vals[-window:]
    n = len(ys)
    if n < 3:
        return 0.0
    xs = range(n)
    mx, my = (n - 1) / 2, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0 or my == 0:
        return 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return slope / my * 100


def analyze(prices):
    """`prices`: list of {'price': float, ...}, oldest first."""
    vals = [float(p["price"]) for p in prices]
    if not vals:
        return None
    cur, lo, hi = vals[-1], min(vals), max(vals)
    avg = sum(vals) / len(vals)
    return {
        "current": round(cur, 2),
        "lowest": round(lo, 2),
        "highest": round(hi, 2),
        "average": round(avg, 2),
        "checks": len(vals),
        "vs_average_pct": round((avg - cur) / avg * 100, 2),  # positive = cheaper than usual
        "vs_highest_pct": round((hi - cur) / hi * 100, 2),    # savings vs peak
        "near_lowest": cur <= lo * 1.02,
        "trend_pct_per_check": round(_slope_pct_per_check(vals), 3),
    }


def rule_based(stats, target_price=None):
    """Returns (verdict, reason)."""
    if stats["checks"] < 5:
        return "WAIT", f"Only {stats['checks']} price checks so far; collecting more history."
    if target_price and stats["current"] <= target_price:
        return "BUY NOW", f"Price is at or below your target of {target_price:,.0f}."
    if stats["near_lowest"] and stats["vs_average_pct"] >= 3:
        return "BUY NOW", (
            f"Within 2% of the lowest tracked price and {stats['vs_average_pct']:.1f}% below average."
        )
    if stats["trend_pct_per_check"] < -0.1:
        return "WAIT", "Price is still trending down; a better deal may be coming."
    if stats["vs_average_pct"] >= 8:
        return "BUY NOW", f"Price is {stats['vs_average_pct']:.1f}% below its average."
    if stats["trend_pct_per_check"] > 0.1 and stats["vs_average_pct"] > 0:
        return "BUY NOW", "Price is rising but still below average; buy before it climbs."
    return "WAIT", "No meaningful discount versus the average price right now."
