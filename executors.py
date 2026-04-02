"""
Aion Executors

Built-in capabilities that skills can reference. These are the
entity's "hands" — generic tools that SKILL.md files teach it
how to use for specific purposes.

Two-pass tool calling: the entity first responds without tool
definitions. If its response expresses tool intent, the server
re-calls with tool definitions enabled and the entity can make
structured tool calls. This prevents reflexive tool use on
conversational messages.

Adding a new executor is a code change (rare).
Adding a new skill that uses existing executors is just a SKILL.md (common).
"""

import json
import logging
import requests
import search_limiter

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


def list_executors() -> list[str]:
    """List all registered executor names."""
    return list(_executors.keys())



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


def get_tool_definitions() -> list[dict]:
    """
    Generate Ollama-compatible tool definitions from registered executors.
    These are the generic tools the entity can use — it reads SKILL.md
    documentation to know when and how to use them.
    """
    definitions = []
    for name, exe in _executors.items():
        definitions.append({
            "type": "function",
            "function": {
                "name": name,
                "description": exe["description"],
                "parameters": exe["parameters"],
            },
        })
    return definitions


# ============================================================
# Built-in executors
# ============================================================

def _http_request(method: str, url: str, headers: str = "",
                  body: str = "", auth_secret: str = "",
                  max_chars: int = 4000) -> str:
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
        text = response.text[:max_chars]
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

    if not search_limiter.can_search():
        return 'Search limit reached for this month. Try again next month or use web_fetch if you have a specific URL.'

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        response = client.search(query, max_results=max_results)
        search_limiter.record_search()

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
    import uuid
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

    doc_id = str(uuid.uuid4())

    # Chunk and embed into ChromaDB (same path as URL ingestion)
    chunk_count = memory.ingest_document(
        doc_id=doc_id,
        text=content,
        title=title,
        source_type=doc_type,
        source_trust=source_trust,
    )

    # Record in DB2 for UI and batch summarization
    db.save_document(
        doc_id=doc_id,
        title=title,
        source_type=doc_type,
        source_trust=source_trust,
        chunk_count=chunk_count,
    )

    return f"Document stored: {title} (type: {doc_type}, {chunk_count} chunks)"


# ============================================================
# Registration
# ============================================================

def init_executors():
    """Register all built-in executors.

    Parameter schemas are retained for documentation and potential
    future use with tool-calling models. Currently, executors are
    called server-side — the model never sees these definitions.
    """
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
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return from response (default 4000)",
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
        "Store a document in your memory system. Use for journal entries, reflections, research notes, or anything worth remembering.",
        {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "description": "Document type: journal, research, article, diagnostic, moltbook, observation",
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
