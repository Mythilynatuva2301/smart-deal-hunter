"""Background worker: wakes up, finds products that are due, and runs the pipeline.

Pipeline per product:
    fetch price (scraper, no LLM) -> save -> stats -> supervisor agent verdict -> alerts

Run on its own:   python worker.py          (loops forever)
                  python worker.py --once   (check every product once, then exit)
The Streamlit app also starts this loop in a background thread by default.
"""
import random
import sys
import time

import agent
import analyzer
import db
import scrapers


def check_product(p: dict):
    """Run the full pipeline for one product. Never raises; errors are stored on the product."""
    try:
        history = db.get_prices(p["id"], 1)
        last = history[-1]["price"] if history else None

        price = scrapers.fetch_price(p, last)  # 1) fetch: deterministic, always runs on schedule
        db.add_price(p["id"], price)
        db.set_error(p["id"], None)

        stats = analyzer.analyze(db.get_prices(p["id"], 500))  # 2) supervisor agent decides
        verdict, reason, source = agent.recommend(p, stats)
        db.add_recommendation(p["id"], verdict, reason, source)

        # 3) deal alerts
        target = p["target_price"]
        if target and price <= target and (last is None or last > target):
            db.add_alert(p["id"], f"{p['name']} hit your target: {price:,.2f} (target {target:,.2f})")
        if last and price <= last * 0.95:
            db.add_alert(p["id"], f"{p['name']} dropped {((last - price) / last * 100):.1f}% to {price:,.2f}")
        if stats["checks"] >= 5 and stats["near_lowest"] and (last is None or last > stats["lowest"] * 1.02):
            db.add_alert(p["id"], f"{p['name']} is at its lowest tracked price: {price:,.2f}")
    except Exception as e:
        db.set_error(p["id"], f"{type(e).__name__}: {e}"[:300])
    finally:
        # Randomised delay inside the user's window (default 12-30 min)
        lo, hi = p["min_interval"], max(p["max_interval"], p["min_interval"])
        db.set_next_check(p["id"], random.uniform(lo, hi))


def run_due():
    for p in db.due_products():
        check_product(p)
        time.sleep(random.uniform(1, 3))  # small gap between sites


def loop(poll_seconds=20):
    db.init_db()
    while True:
        try:
            run_due()
        except Exception as e:  # keep the worker alive no matter what
            print("worker error:", e, file=sys.stderr)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    db.init_db()
    if "--once" in sys.argv:
        for prod in db.list_products():
            check_product(prod)
        print("Checked", len(db.list_products()), "product(s).")
    else:
        print("Worker running. Ctrl+C to stop.")
        loop()
