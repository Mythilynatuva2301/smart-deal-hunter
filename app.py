"""Smart Deal Hunter dashboard.  Run with:  streamlit run app.py"""
import os
import threading
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import agent
import analyzer
import db
import worker

TZ = os.getenv("APP_TZ", "Asia/Kolkata")
CUR = os.getenv("CURRENCY", "₹")

st.set_page_config(page_title="Smart Deal Hunter", page_icon="🛍️", layout="wide")
db.init_db()


@st.cache_resource
def start_embedded_worker():
    """Starts the price-checking loop once per server process."""
    if os.getenv("RUN_EMBEDDED_WORKER", "1") == "1":
        threading.Thread(target=worker.loop, daemon=True, name="deal-worker").start()
        return True
    return False


embedded = start_embedded_worker()


def to_local(ts: str) -> pd.Timestamp:
    return pd.Timestamp(ts).tz_localize("UTC").tz_convert(TZ)


def fmt_time(ts):
    return to_local(ts).strftime("%d %b, %I:%M %p") if ts else "-"


# ---------------- sidebar: add product ----------------
with st.sidebar:
    st.header("➕ Track a product")
    with st.form("add", clear_on_submit=True):
        name = st.text_input("Product name", placeholder="Sony WH-1000XM5")
        mode = st.selectbox(
            "Data source",
            ["mock", "http", "browser"],
            help="mock = simulated prices (demo). http = plain page fetch. "
            "browser = headless Chromium for JavaScript-heavy pages.",
        )
        url = st.text_input("Product URL (http/browser)")
        selector = st.text_input("CSS selector of the price (http/browser)", placeholder="span.price")
        target = st.number_input(f"Target price ({CUR}, 0 = none)", min_value=0.0, step=100.0)
        c1, c2 = st.columns(2)
        lo = c1.number_input("Min interval (min)", 1, 240, 12)
        hi = c2.number_input("Max interval (min)", 1, 240, 30)
        if st.form_submit_button("Start tracking", use_container_width=True):
            if not name.strip():
                st.error("Enter a product name.")
            elif mode != "mock" and not (url and selector):
                st.error("URL and CSS selector are required for http/browser mode.")
            else:
                db.add_product(name.strip(), mode, url or None, selector or None,
                               target or None, int(lo), int(max(hi, lo)))
                st.success("Tracking started. First check happens within seconds.")
    st.caption(
        "Background worker: running inside this app." if embedded
        else "Embedded worker is off. Run `python worker.py` in another terminal."
    )
    st.caption("Only track sites that allow automated access.")


# ---------------- header + chat with the supervisor agent ----------------
st.title("🛍️ Smart Deal Hunter")

with st.expander("💬 Ask the Deal Hunter (supervisor agent)"):
    q = st.text_input("Your question", placeholder="Is Sony WH-1000XM5 worth buying now? How much below average is it?")
    if st.button("Ask", key="ask") and q.strip():
        with st.spinner("Supervisor is coordinating its agents..."):
            st.session_state["answer"] = agent.ask(q.strip())
    if st.session_state.get("answer"):
        st.markdown(st.session_state["answer"])


# ---------------- main dashboard (auto-refreshes) ----------------
@st.fragment(run_every=60)
def dashboard():
    st.caption(f"Last refreshed {datetime.now().strftime('%I:%M:%S %p')}  ·  dashboard re-reads the database every 60 s")

    products = db.list_products()
    left, right = st.columns([3, 1])

    with right:
        st.subheader("🔔 Deal alerts")
        alerts = db.recent_alerts()
        if not alerts:
            st.caption("No alerts yet.")
        for a in alerts:
            st.info(f"{a['message']}\n\n_{fmt_time(a['created_at'])}_")

    with left:
        if not products:
            st.info("Add a product in the sidebar to get started. Pick **mock** to see it work without a website.")
        for p in products:
            history = db.get_prices(p["id"], 500)
            with st.container(border=True):
                top = st.columns([4, 1, 1])
                top[0].subheader(p["name"])
                if top[1].button("Check now", key=f"chk{p['id']}"):
                    with st.spinner("Checking..."):
                        worker.check_product(p)
                    st.rerun(scope="fragment")
                if top[2].button("Delete", key=f"del{p['id']}"):
                    db.delete_product(p["id"])
                    st.rerun(scope="fragment")

                if not history:
                    st.caption("Waiting for the first price check...")
                    if p["last_error"]:
                        st.error(p["last_error"])
                    continue

                s = analyzer.analyze(history)
                rec = db.latest_recommendation(p["id"])
                m = st.columns(5)
                m[0].metric("Current", f"{CUR}{s['current']:,.0f}",
                            delta=f"{-(s['vs_average_pct']):.1f}% vs avg", delta_color="inverse")
                m[1].metric("Lowest", f"{CUR}{s['lowest']:,.0f}")
                m[2].metric("Average", f"{CUR}{s['average']:,.0f}")
                m[3].metric("Highest", f"{CUR}{s['highest']:,.0f}")
                m[4].metric("Checks", s["checks"])

                if rec:
                    box = st.success if rec["verdict"] == "BUY NOW" else st.warning
                    tag = "AI agents" if rec["source"] == "agents" else "rules"
                    box(f"**{rec['verdict']}**: {rec['reason']}  \n_({tag} · {fmt_time(rec['created_at'])})_")

                df = pd.DataFrame(history)
                df["time"] = df["checked_at"].map(to_local)
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df["time"], y=df["price"], mode="lines+markers", name="Price"))
                fig.add_hline(y=s["average"], line_dash="dash", line_color="gray", annotation_text="average")
                if p["target_price"]:
                    fig.add_hline(y=p["target_price"], line_dash="dot", line_color="green", annotation_text="target")
                fig.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0), yaxis_title=CUR)
                st.plotly_chart(fig, use_container_width=True, key=f"fig{p['id']}")

                st.caption(
                    f"Source: {p['mode']}  ·  every {p['min_interval']}-{p['max_interval']} min  ·  "
                    f"next check ≈ {fmt_time(p['next_check_at'])}"
                )
                if p["last_error"]:
                    st.error(f"Last check failed: {p['last_error']}")


dashboard()
