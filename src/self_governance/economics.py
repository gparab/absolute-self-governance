"""Economic governance and model routing module.

Provides budgetary management using TaskWallet, and adaptive model routing using
AST-based code complexity checking and task analysis keywords.
"""

import logging
import ast
from typing import Optional

logger = logging.getLogger("self_governance.economics")


class BudgetExceededError(ValueError):
    """Exception raised when the cumulative session spent exceeds the maximum budget."""
    pass


class TaskWallet:
    """A virtual wallet tracking token costs.

    Enforces maximum budget constraints and throws BudgetExceededError on violation.
    """

    def __init__(self, max_budget: float = 0.50):
        """Initializes the TaskWallet.

        Args:
            max_budget: The maximum allowed USD budget limit.
        """
        self.max_budget = max_budget
        self.spent = 0.0

    def charge(self, cost: float) -> None:
        """Charges a given cost to the wallet.

        Args:
            cost: The USD cost to deduct from budget.

        Raises:
            BudgetExceededError: If the cumulative spent exceeds the max_budget.
        """
        self.spent += cost
        logger.info(f"Charged wallet: cost=${cost:.6f}, total spent=${self.spent:.6f}, budget=${self.max_budget:.6f}")
        if self.spent > self.max_budget:
            raise BudgetExceededError(
                f"Budget of ${self.max_budget:.6f} exceeded. Spent: ${self.spent:.6f}."
            )


def analyze_ast_complexity(code: str) -> Optional[str]:
    """Analyzes the Python code AST to determine its complexity tier.

    Inspects code metrics (nodes count, loop count, classes/functions count, try blocks)
    and module imports (network, crypto, concurrency) to recommend an LLM model routing tier.

    Args:
        code: A string of Python source code to analyze.

    Returns:
        A recommended model routing string (e.g., 'gemini-2.5-pro',
        'gemini-1.5-pro', or 'gemini-1.5-flash'), or None if parsing fails/empty code.
    """
    # Check if the code is empty or just whitespace
    if not code.strip():
        return None
    try:
        tree = ast.parse(code)
    except Exception:
        return None

    nodes = list(ast.walk(tree))
    node_count = len(nodes)

    loops = 0
    functions = 0
    classes = 0
    tries = 0

    concurrency_modules = {"threading", "multiprocessing", "asyncio", "concurrent"}
    network_modules = {"socket", "urllib", "requests", "http", "aiohttp", "select"}
    crypto_modules = {"ssl", "cryptography", "hashlib", "hmac", "secrets"}

    has_concurrency = False
    has_network = False
    has_crypto = False

    for node in nodes:
        if isinstance(node, (ast.For, ast.While)):
            loops += 1
        elif isinstance(node, ast.FunctionDef):
            functions += 1
        elif isinstance(node, ast.ClassDef):
            classes += 1
        elif isinstance(node, (ast.Try, ast.TryStar, ast.ExceptHandler)):
            tries += 1

        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split('.')[0]
                if name in concurrency_modules:
                    has_concurrency = True
                if name in network_modules:
                    has_network = True
                if name in crypto_modules:
                    has_crypto = True
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                name = node.module.split('.')[0]
                if name in concurrency_modules:
                    has_concurrency = True
                if name in network_modules:
                    has_network = True
                if name in crypto_modules:
                    has_crypto = True
        elif isinstance(node, ast.Name):
            if node.id in concurrency_modules:
                has_concurrency = True
            if node.id in network_modules:
                has_network = True
            if node.id in crypto_modules:
                has_crypto = True
        elif isinstance(node, ast.Attribute):
            if node.attr in concurrency_modules:
                has_concurrency = True
            if node.attr in network_modules:
                has_network = True
            if node.attr in crypto_modules:
                has_crypto = True

    # Tier 3: Concurrency, network, cryptography/security, or >150 nodes
    if node_count > 150 or has_concurrency or has_network or has_crypto:
        return "gemini-2.5-pro"

    # Tier 2: Typical classes, multiple functions, or error handling
    if classes > 0 or functions > 1 or tries > 0:
        return "gemini-1.5-pro"

    # Tier 1: Low complexity (simple variable assignments, low loop count, few nodes)
    return "gemini-1.5-flash"


def route_model(task_type: str, code_snippet: Optional[str] = None) -> str:
    """Adaptive model router.

    Routes based on AST code complexity if a code snippet is provided or if task_type
    is parseable code.
    Routine formatting, linting, parsing, and basic checks route to "gemini-1.5-flash".
    Planning, critiquing, and complex reasoning route to "gemini-1.5-pro".

    Args:
        task_type: Description of the task or raw code if task is execution.
        code_snippet: Optional code snippet string to route against.

    Returns:
        The identifier of the selected LLM model.
    """
    if code_snippet is not None:
        tier = analyze_ast_complexity(code_snippet)
        if tier:
            return tier

    tier = analyze_ast_complexity(task_type)
    if tier:
        return tier

    task_lower = task_type.lower()

    routine_keywords = ["format", "lint", "parse", "check", "routine", "syntax", "style"]
    complex_keywords = ["plan", "critique", "reason", "analyze", "consensus", "succession", "complex"]

    if any(kw in task_lower for kw in complex_keywords):
        return "gemini-1.5-pro"
    if any(kw in task_lower for kw in routine_keywords):
        return "gemini-1.5-flash"

    return "gemini-1.5-pro"

