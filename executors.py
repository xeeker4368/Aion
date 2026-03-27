"""
Aion Executors

Built-in capabilities that skills can reference. These are the
entity's "hands" — generic tools that SKILL.md files teach it
how to use for specific purposes.

Executors are registered as Ollama tool functions so the model
can invoke them during conversation. The application handles
the actual execution.

Adding a new executor is a code change (rare).
Adding a new skill that uses existing executors is just a SKILL.md (common).
"""

import json
import logging
from urllib.parse import quote_plus

import requests

import vault

logger = logging.getLogger("aion.executors")

# Registry of available executors
_executors: dict[str, dict] = {}


def register(name: str, func, description: str, parameters: dict):
    """Register an executor with its tool definition."""
    _executors[name] = {
        "func": func,
        "description": description,
        "parameters": parameters,
    }


def get_executor(name: str):
    """Get an executor by name."""
    return _executors.get(name)


def list_executors() -> list[str]:
    """List all registered executor names."""
    return list(_executors.keys())


def get_tool_definitions() -> list[dict]:
    """
    Get all executors as Ollama tool definitions.
    These get passed to the model so it can call them.
    """
    tools = []
    for name, exe in _executors.items():
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": exe["description"],
                "parameters": exe["parameters"],
            },
        })
    return tools


def execute(name: str, arguments: dict) -> str:
    """
    Execute a tool by name with the given arguments.
    Returns the result as a string for the model to read.
    """
    exe = _executors.get(name)
    if not exe:
        return f"Error: executor '{name}' not found."

    try:
        result = exe["func"](**arguments)
        return result
    except Exception as e:
        logger.error(f"Executor '{name}' failed: {e}")
        return f"Error executing {name}: {str(e)}"


# ============================================================
# Built-in executors
# ============================================================

def _http_request(method: str, url: str, headers: str = "",
                  body: str = "", auth_secret: str = "") -> str:
    """
    Make an HTTP request. Used by API-based skills like Moltbook.

    Args:
        method: GET, POST, PUT, DELETE
        url: The full URL
        headers: JSON string of headers (optional)
        body: JSON string of request body (optional)
        auth_secret: Name of secret to use as Bearer token (optional)
    """
    try:
        req_headers = json.loads(headers) if headers else {}
    except json.JSONDecodeError:
        req_headers = {}

    try:
        req_body = json.loads(body) if body else None
    except json.JSONDecodeError:
        req_body = body  # Send as raw string if not valid JSON

    # Add auth if specified
    if auth_secret:
        token = vault.get(auth_secret)
        if token:
            req_headers["Authorization"] = f"Bearer {token}"
        else:
            return f"Error: secret '{auth_secret}' not found. Add it in Settings."

    req_headers.setdefault("Content-Type", "application/json")

    try:
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=req_headers,
            json=req_body if isinstance(req_body, dict) else None,
            data=req_body if isinstance(req_body, str) else None,
            timeout=30,
        )

        # Truncate long responses to avoid blowing up context
        text = response.text[:4000]
        return f"HTTP {response.status_code}\n{text}"

    except requests.RequestException as e:
        return f"HTTP request failed: {str(e)}"


def _web_fetch(url: str, max_chars: int = 8000) -> str:
    """
    Fetch a URL and extract readable text content.
    Used by web search skills and for reading web pages.

    Args:
        url: The URL to fetch
        max_chars: Maximum characters to return
    """
    try:
        response = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Aion/1.0"},
        )
        response.raise_for_status()

        # Basic text extraction — strip HTML tags
        text = response.text

        # Simple HTML tag removal (good enough for DDG Lite and most pages)
        import re
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        return text[:max_chars]

    except requests.RequestException as e:
        return f"Failed to fetch URL: {str(e)}"


def _web_search(query: str, max_results: int = 5) -> str:
    """
    Search the web using Tavily. Requires TAVILY_API_KEY in vault.

    Args:
        query: The search query
        max_results: Maximum number of results (default 5)
    """
    api_key = vault.get("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY not found. Add it in Settings (/settings)."

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=max_results)

        results = response.get("results", [])
        if not results:
            return "No results found."

        lines = []
        for r in results:
            lines.append(f"Title: {r.get('title', 'No title')}")
            lines.append(f"URL: {r.get('url', '')}")
            lines.append(f"Summary: {r.get('content', 'No content')}")
            lines.append("")

        return "\n".join(lines)[:4000]

    except Exception as e:
        return f"Search failed: {str(e)}"


def _store_document(doc_type: str, title: str, content: str) -> str:
    """
    Store a document in the memory system.
    Used by skills that produce knowledge worth remembering.

    Args:
        doc_type: Type of document (research, article, diagnostic, moltbook, etc.)
        title: A short title for the document
        content: The document content
    """
    import db
    import memory

    # Map doc_type to source trust level
    trust_map = {
        "journal": "firsthand",
        "creative": "firsthand",
        "diagnostic": "firsthand",
        "observation": "secondhand",
        "research": "secondhand",
        "moltbook": "thirdhand",
        "article": "thirdhand",
    }
    source_trust = trust_map.get(doc_type, "secondhand")

    # Store in DB1+DB2 as ground truth
    doc_id = db.start_conversation()
    db.save_message(doc_id, "system", f"[{doc_type}] {title}\n\n{content}")
    db.end_conversation(doc_id)

    # Chunk and embed with source metadata
    messages = db.get_conversation_messages(doc_id)
    memory.create_live_chunk(
        conversation_id=doc_id,
        messages=messages,
        chunk_index=0,
        source_type=doc_type,
        source_trust=source_trust,
    )

    return f"Document stored: {title} (type: {doc_type})"


# ============================================================
# Registration
# ============================================================

def init_executors():
    """Register all built-in executors."""
    register(
        "http_request",
        _http_request,
        "Make an HTTP request to an API. Use for interacting with external services.",
        {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "description": "HTTP method: GET, POST, PUT, DELETE",
                },
                "url": {
                    "type": "string",
                    "description": "The full URL to request",
                },
                "headers": {
                    "type": "string",
                    "description": "JSON string of request headers (optional)",
                },
                "body": {
                    "type": "string",
                    "description": "JSON string of request body (optional)",
                },
                "auth_secret": {
                    "type": "string",
                    "description": "Name of stored secret to use as Bearer token (optional)",
                },
            },
            "required": ["method", "url"],
        },
    )

    register(
        "web_fetch",
        _web_fetch,
        "Fetch a web page and extract readable text. Use for reading articles, documentation, or any URL.",
        {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return (default 8000)",
                },
            },
            "required": ["url"],
        },
    )

    register(
        "web_search",
        _web_search,
        "Search the web using Tavily. Use when you need current information you don't have in memory.",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default 5)",
                },
            },
            "required": ["query"],
        },
    )

    register(
        "store_document",
        _store_document,
        "Store a document in your memory system. Use when you learn something worth remembering from research, articles, or interactions.",
        {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "description": "Document type: research, article, diagnostic, moltbook, observation",
                },
                "title": {
                    "type": "string",
                    "description": "Short title for the document",
                },
                "content": {
                    "type": "string",
                    "description": "The document content",
                },
            },
            "required": ["doc_type", "title", "content"],
        },
    )

    logger.info(f"Registered {len(_executors)} executors: {list_executors()}")
