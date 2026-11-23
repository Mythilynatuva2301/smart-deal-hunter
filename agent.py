"""Multi-agent layer using the supervisor pattern (same design as Supervised_agent.ipynb).

    Supervisor ReAct agent
      ├── price_research_agent  -> sub-agent with tool: fetch_current_price
      ├── price_analysis_agent  -> sub-agent with tools: list_tracked_products, get_price_stats
      └── math_agent            -> sub-agent with tool: safe_math (discount / savings maths)

Each sub-agent is a ReAct agent + AgentExecutor. It is wrapped in a plain function, turned
into a StructuredTool, and handed to the supervisor, which decides who to call.

Everything that must be reliable (fetching on schedule, saving prices) is done by the worker
without an LLM. The agents are used for the Buy Now / Wait decision and for the chat box.
If the LLM is unavailable or answers in an unexpected format, the rule-based analyzer is used.
"""
import ast
import json
import operator
import os
import re
import threading

import analyzer
import db
import scrapers

VERBOSE = os.getenv("AGENT_VERBOSE", "0") == "1"
USE_SUPERVISOR = os.getenv("USE_SUPERVISOR", "1") == "1"


# ======================= tools (plain, deterministic functions) =======================
def _product_id(value) -> int:
    m = re.search(r"\d+", str(value))
    if not m:
        raise ValueError("a numeric product id is required")
    return int(m.group())


def _get_product(pid: int):
    return next((p for p in db.list_products() if p["id"] == pid), None)


def list_tracked_products(_: str = "") -> str:
    """List every tracked product with its id and target price."""
    rows = db.list_products()
    if not rows:
        return "No products are being tracked yet."
    return "\n".join(f"id={p['id']} | {p['name']} | target_price={p['target_price']}" for p in rows)


def get_price_stats(product_id: str) -> str:
    """Price statistics (current, lowest, highest, average, trend, discount vs average) as JSON."""
    try:
        pid = _product_id(product_id)
        product = _get_product(pid)
        history = db.get_prices(pid, 500)
        if not product:
            return f"Error: no product with id {pid}."
        if not history:
            return "No price history yet for this product."
        stats = analyzer.analyze(history)
        stats["target_price"] = product["target_price"]
        return json.dumps(stats)
    except Exception as e:
        return f"Error: {e}"


def fetch_current_price(product_id: str) -> str:
    """Visit the product's source right now, save the new price, and return it."""
    try:
        pid = _product_id(product_id)
        product = _get_product(pid)
        if not product:
            return f"Error: no product with id {pid}."
        history = db.get_prices(pid, 1)
        price = scrapers.fetch_price(product, history[-1]["price"] if history else None)
        db.add_price(pid, price)
        return f"Current price of {product['name']}: {price}"
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    raise ValueError("unsupported expression")


def safe_math(expression: str) -> str:
    """Evaluate arithmetic such as (50000-42000)/50000*100. Does not use eval()."""
    try:
        tree = ast.parse(expression.strip().replace(",", ""), mode="eval")
        return str(round(_eval(tree.body), 4))
    except Exception as e:
        return f"Error: {e}"


# ======================= agents =======================
REACT_TEMPLATE = """Answer the following questions as best you can. You have access to the following tools:

{tools}

Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original input question

Begin!

Question: {input}
Thought:{agent_scratchpad}"""

_supervisor = None
_lock = threading.Lock()


def _build_supervisor():
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.tools import StructuredTool
    from langchain_groq import ChatGroq

    try:
        from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
    except ImportError:  # older LangChain versions
        from langchain.agents import AgentExecutor, create_tool_calling_agent

    llm = ChatGroq(model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), temperature=0)
    # Tool-calling agents use the model's native function calling, so there is no
    # fragile "Thought/Action" text format to get wrong (unlike the ReAct prompt).
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a careful assistant. Use your tools to get facts instead of guessing. "
                   "When you have what you need, reply with the final answer only."),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])

    def tool(func, name, description):
        return StructuredTool.from_function(func=func, name=name, description=description)

    def executor(tools, max_iterations):
        agent = create_tool_calling_agent(llm, tools, prompt)
        return AgentExecutor(
            agent=agent, tools=tools, verbose=VERBOSE,
            handle_parsing_errors=True, max_iterations=max_iterations,
        )

    # --- sub-agents (each is a ReAct agent with its own tools) ---
    price_exe = executor(
        [tool(fetch_current_price, "fetch_current_price",
              "Visit the product's source now and save the newest price. Input: the numeric product id.")], 4)
    analysis_exe = executor(
        [tool(list_tracked_products, "list_tracked_products",
              "List tracked products with their ids. Input: an empty string."),
         tool(get_price_stats, "get_price_stats",
              "Get price statistics as JSON (current, lowest, highest, average, vs_average_pct, "
              "near_lowest, trend_pct_per_check, target_price). Input: the numeric product id.")], 6)
    math_exe = executor(
        [tool(safe_math, "safe_math",
              "Evaluate an arithmetic expression, e.g. (50000-42000)/50000*100. Input: the expression only.")], 4)

    # --- wrapper functions: run a sub-agent and return its output (as in your notebook) ---
    def price_research_wrapper(question: str) -> str:
        return price_exe.invoke({"input": question})["output"]

    def price_analysis_wrapper(question: str) -> str:
        return analysis_exe.invoke({"input": question})["output"]

    def math_wrapper(question: str) -> str:
        return math_exe.invoke({"input": question})["output"]

    supervisor_tools = [
        tool(price_research_wrapper, "price_research_agent",
             "Fetches the latest live price of a product. Ask it e.g. 'fetch the current price of product id 3'."),
        tool(price_analysis_wrapper, "price_analysis_agent",
             "Looks up tracked products and their price history statistics. Ask it e.g. "
             "'give the price stats for product id 3' or 'list tracked products'."),
        tool(math_wrapper, "math_agent",
             "Does discount and savings calculations. Ask it e.g. 'what is (50000-42000)/50000*100?'."),
    ]
    return executor(supervisor_tools, 8)


def get_supervisor():
    global _supervisor
    with _lock:
        if _supervisor is None:
            _supervisor = _build_supervisor()
        return _supervisor


# ======================= public API =======================
TASK = (
    "Product id {pid} ('{name}'). Shopper target price: {target}. "
    "Ask the price analysis agent for the latest price statistics of this product id "
    "(use the math agent for any discount calculation you need). Then decide whether the "
    "shopper should BUY NOW or WAIT for a better deal. Be conservative: say BUY NOW only when the "
    "price is clearly good (near its lowest, well below average, or at/below the target). "
    "Do NOT fetch a new live price. Your Final Answer must be exactly one line in this format: "
    "VERDICT: BUY NOW or WAIT | REASON: one or two short sentences."
)
CHAT_PREFIX = (
    "You are Smart Deal Hunter, a friendly shopping assistant. You can answer ANY question: product "
    "features, comparisons, buying advice, general knowledge. Answer general questions directly from "
    "your own knowledge, without using any agent. Only use your agents when the question is about "
    "the user's tracked products or their prices (for a product id, ask the price analysis agent to "
    "list tracked products). If you are not sure about a product's specs, say so instead of guessing. "
    "Keep the answer clear and practical."
)
_VERDICT_RE = re.compile(r"VERDICT:\s*(BUY NOW|WAIT)\s*\|\s*REASON:\s*(.+)", re.I | re.S)


def parse_verdict(text: str):
    m = _VERDICT_RE.search(text or "")
    if not m:
        return None
    return m.group(1).upper(), " ".join(m.group(2).split())


def recommend(product: dict, stats: dict):
    """Returns (verdict, reason, source); source is 'agents' or 'rules'."""
    target = product.get("target_price")
    if USE_SUPERVISOR and os.getenv("GROQ_API_KEY") and stats["checks"] >= 5:
        try:
            out = get_supervisor().invoke(
                {"input": TASK.format(pid=product["id"], name=product["name"], target=target or "not set")}
            )["output"]
            parsed = parse_verdict(out)
            if parsed:
                return parsed[0], parsed[1], "agents"
            raise ValueError("supervisor answer was not in the expected format")
        except Exception as e:
            verdict, reason = analyzer.rule_based(stats, target)
            return verdict, f"{reason} (agents unavailable: {type(e).__name__})", "rules"
    verdict, reason = analyzer.rule_based(stats, target)
    return verdict, reason, "rules"


def ask(question: str) -> str:
    """Free-form chat with the supervisor (used by the dashboard's chat box)."""
    if not os.getenv("GROQ_API_KEY"):
        return "Set GROQ_API_KEY in your .env file to chat with the agents."
    try:
        return get_supervisor().invoke({"input": f"{CHAT_PREFIX}\n\nUser question: {question}"})["output"]
    except Exception as e:
        return f"The agents ran into a problem ({type(e).__name__}: {e})."
