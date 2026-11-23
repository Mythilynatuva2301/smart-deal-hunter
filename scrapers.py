"""Scraper agent: gets the current price of one product.

Modes
-----
mock    : simulated price random walk. Use this for demos and development.
http    : plain requests + BeautifulSoup. Works for server-rendered pages.
browser : headless Chromium via Playwright. Works for JavaScript-rendered pages.

Only point this at sites whose terms allow automated access. The http mode checks
robots.txt before fetching.
"""
import random
import re
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SmartDealHunter/1.0"
)


def parse_price(text: str) -> float:
    """'₹1,299.00' -> 1299.0, 'Rs. 49,999' -> 49999.0"""
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    if not match:
        raise ValueError(f"No price found in text: {text!r}")
    return float(match.group(0).replace(",", ""))


def mock_price(last):
    rnd = random.Random()
    if last is None:
        return float(round(rnd.uniform(1000, 50000), -1))
    change = rnd.gauss(0, 0.015)
    if rnd.random() < 0.08:  # occasional flash sale
        change -= rnd.uniform(0.05, 0.15)
    if rnd.random() < 0.05:  # occasional price bounce back
        change += rnd.uniform(0.04, 0.10)
    return round(max(last * (1 + change), 1.0), 2)


def robots_allowed(url: str) -> bool:
    parts = urlparse(url)
    rp = RobotFileParser()
    rp.set_url(f"{parts.scheme}://{parts.netloc}/robots.txt")
    try:
        rp.read()
    except Exception:
        return True  # robots.txt unreachable: don't block
    return rp.can_fetch(UA, url)


def http_price(url: str, selector: str) -> float:
    import requests
    from bs4 import BeautifulSoup

    if not robots_allowed(url):
        raise PermissionError("robots.txt disallows fetching this URL")
    resp = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}, timeout=20)
    resp.raise_for_status()
    node = BeautifulSoup(resp.text, "html.parser").select_one(selector)
    if node is None:
        raise ValueError(f"Selector {selector!r} not found (page may be JavaScript-rendered; try browser mode)")
    return parse_price(node.get_text(" ", strip=True))


def browser_price(url: str, selector: str) -> float:
    from playwright.sync_api import sync_playwright

    if not robots_allowed(url):
        raise PermissionError("robots.txt disallows fetching this URL")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA)
            page.goto(url, timeout=45_000, wait_until="domcontentloaded")
            page.wait_for_selector(selector, timeout=15_000)
            return parse_price(page.inner_text(selector))
        finally:
            browser.close()


def fetch_price(product: dict, last_price=None) -> float:
    mode = product["mode"]
    if mode == "mock":
        return mock_price(last_price)
    if not product.get("url") or not product.get("selector"):
        raise ValueError("URL and CSS selector are required for http/browser mode")
    if mode == "http":
        return http_price(product["url"], product["selector"])
    if mode == "browser":
        return browser_price(product["url"], product["selector"])
    raise ValueError(f"Unknown mode: {mode}")
