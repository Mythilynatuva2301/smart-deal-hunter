🛍️ Smart Deal Hunter
An AI shopping assistant that tracks product prices, builds a price history, and tells you
whether to Buy Now or Wait for a better deal.
https://smart-deal-hunter-4qeb5536wbfugt8gm4jx7j.streamlit.app/

Features
Price tracking on a randomised 12-30 minute schedule per product
Price history, lowest / highest / average price and trend analysis
Discount and savings calculation versus the average price
Supervisor multi-agent system (LangChain + Groq) that recommends Buy Now or Wait
Rule-based fallback, so a verdict is always produced even if the LLM fails
Deal alerts: target price reached, price drop of 5% or more, new lowest price
Streamlit dashboard with Plotly charts, auto-refresh and an AI chat box
Tech stack
Python, LangChain, Groq LLM, AI agents, SQLite, Streamlit, Plotly, pandas, requests, BeautifulSoup,
Playwright (optional).
How it works
```
worker (every 12-30 min, random delay)
   │
   ├── Scraper: mock / http / browser           (no LLM involved)
   │        └─▶ saves the new price in SQLite
   │
   ├── Analyzer: current, lowest, highest, average, trend, discount vs average
   │
   └── Supervisor agent (Groq)                  falls back to rules if it fails
          ├── price_research_agent  → fetch_current_price
          ├── price_analysis_agent  → list_tracked_products, get_price_stats
          └── math_agent            → safe_math
                   │
                   ▼
        BUY NOW / WAIT + reason → alerts → Streamlit dashboard (refreshes every 60 s)
```
Each sub-agent is an `AgentExecutor` wrapped in a function and exposed to the supervisor as a
`StructuredTool`. The agents use LangChain's tool-calling API (`create_tool_calling_agent`).
Quick start
```bash
python -m venv venv
venv\Scripts\activate          # Windows  (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
```
Create a file named `.env` in the project folder:
```
GROQ_API_KEY=enter key here
GROQ_MODEL=openai/gpt-oss-120b
```
Run the app:
```bash
streamlit run app.py
```
In the sidebar, add a product with data source mock and set both intervals to 1 minute to see
prices arrive quickly. After 5 price checks the verdict comes from the supervisor agents instead of
the rule-based analyzer.
Data sources
Mode	Use when	Notes
`mock`	Demos and development	Simulated prices with occasional sales
`http`	Price is in the page's HTML	Needs a URL and a CSS selector, checks `robots.txt` first
`browser`	Price is rendered by JavaScript	Needs `pip install playwright` and `playwright install chromium`
Try the `http` mode on the practice site
`https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html` with the selector
`p.price_color`.
Note: many large retailers prohibit scraping and block bots. Only track sites that allow
automated access, or use an official or third-party price API.
Project structure
File	Role
`app.py`	Streamlit dashboard, charts, alerts and chat box
`worker.py`	Scheduler loop: fetch, analyse, recommend, alert
`agent.py`	Supervisor and sub-agents, tools, verdict parsing, chat
`analyzer.py`	Price statistics and the rule-based verdict
`scrapers.py`	mock / http / browser price fetching
`db.py`	SQLite schema and queries
Limitations
Prices are simulated unless you connect a real source.
No currency conversion: the app shows one currency symbol for every product.
The app must be running for prices to be collected.
All users share one database and there is no login.
Roadmap
Price API integration (for example Google Shopping results)
Telegram or email alerts
Multi-store price comparison
Automated tests and always-on hosting
