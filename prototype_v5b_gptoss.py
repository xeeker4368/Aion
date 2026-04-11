"""
Prototype v5b: Let GPT-OSS Decide

Same as v5 but uses gpt-oss:20b instead of llama3.1:8b-aion.
Tests whether the larger overnight model makes better decisions
about when to search memory, use tools, or respond directly.

NOTE: gpt-oss:20b does NOT have SOUL.md baked in. We're testing
the decision-making capability, not the entity-as-itself.
SOUL.md is still loaded into context as text.

READ-ONLY.

Run on Hades:
    cd ~/aion
    ./aion/bin/python prototype_v5b_gptoss.py
"""

import sqlite3
import sys
import re
from pathlib import Path
from collections import defaultdict

CANDIDATES = [
    Path(__file__).parent,
    Path.home() / "aion",
    Path("/home/localadmin/aion"),
]

AION_DIR = None
for candidate in CANDIDATES:
    if (candidate / "soul.md").exists() and (candidate / "server.py").exists():
        AION_DIR = candidate
        break

if AION_DIR is None:
    print("ERROR: Cannot find the aion project directory.")
    sys.exit(1)

sys.path.insert(0, str(AION_DIR))

import ollama
import chromadb
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
from rank_bm25 import BM25Okapi

# ============================================================
# Configuration — ONLY DIFFERENCE FROM v5: model name
# ============================================================

OLLAMA_HOST = "http://localhost:11434"
CHAT_MODEL = "gpt-oss:20b"            # <-- changed from llama3.1:8b-aion
EMBED_MODEL = "nomic-embed-text"
CONTEXT_WINDOW = 16384                # <-- bumped for the larger model
RETRIEVAL_MAX_DISTANCE = 0.75
RETRIEVAL_RESULTS = 5
RRF_K = 60

WORKING_DB = AION_DIR / "data" / "prod" / "working.db"
CHROMA_DIR = str(AION_DIR / "data" / "prod" / "chromadb")
SOUL_PATH = AION_DIR / "soul.md"

TARGET_CONV_PREFIX = "33158f80"
RELAY_CONV_PREFIX = "bfc118df"


# ============================================================
# Connections (identical to v5)
# ============================================================

def connect_db():
    conn = sqlite3.connect(str(WORKING_DB))
    conn.row_factory = sqlite3.Row
    return conn

def connect_chroma():
    ef = OllamaEmbeddingFunction(url=OLLAMA_HOST, model_name=EMBED_MODEL)
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    return client.get_or_create_collection(
        name="aion_memory", embedding_function=ef,
        metadata={"hnsw:space": "cosine"})

def load_soul():
    return SOUL_PATH.read_text() if SOUL_PATH.exists() else ""

def load_self_knowledge(conn):
    row = conn.execute(
        "SELECT content FROM self_knowledge ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return row["content"] if row else ""

def load_conversation(conn, conv_prefix):
    row = conn.execute("SELECT id FROM conversations WHERE id LIKE ?",
                        (f"{conv_prefix}%",)).fetchone()
    if not row: return None, []
    full_id = row["id"]
    msgs = conn.execute(
        "SELECT role, content, timestamp FROM messages "
        "WHERE conversation_id = ? ORDER BY timestamp",
        (full_id,)).fetchall()
    return full_id, [dict(m) for m in msgs]

def format_timestamp(ts):
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts).strftime("%B %d, %Y at %I:%M %p")
    except: return ts


# ============================================================
# BM25 + search (identical to v5)
# ============================================================

def _tokenize(text):
    return [w for w in re.findall(r'[a-zA-Z]+', text.lower()) if len(w) > 1]

def build_bm25_index(collection):
    all_data = collection.get(include=["documents", "metadatas"])
    chunk_list = []
    tokenized = []
    for i, doc in enumerate(all_data["documents"]):
        meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
        chunk_list.append({
            "id": all_data["ids"][i],
            "text": doc,
            "conversation_id": meta.get("conversation_id", ""),
            "source_type": meta.get("source_type", ""),
            "source_trust": meta.get("source_trust", ""),
        })
        tokenized.append(_tokenize(doc))
    return BM25Okapi(tokenized), chunk_list

def search_vector(collection, query, n_results=RETRIEVAL_RESULTS):
    trust_weights = {"firsthand": 0.9, "secondhand": 1.0, "thirdhand": 1.1}
    fetch_count = min(n_results * 3, collection.count())
    try:
        results = collection.query(query_texts=[query], n_results=fetch_count)
    except: return []
    if not results or not results["documents"][0]: return []

    memories = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i]
        dist = results["distances"][0][i]
        trust = meta.get("source_trust", "firsthand")
        weighted = dist * trust_weights.get(trust, 1.0)
        memories.append({
            "id": results["ids"][0][i],
            "text": doc, "conversation_id": meta.get("conversation_id", ""),
            "weighted_distance": weighted, "source_type": meta.get("source_type", ""),
        })
    memories.sort(key=lambda m: m["weighted_distance"] or float("inf"))

    seen = set()
    deduped = []
    for mem in memories:
        if mem["weighted_distance"] and mem["weighted_distance"] > RETRIEVAL_MAX_DISTANCE: break
        cid = mem["conversation_id"]
        if cid not in seen:
            seen.add(cid)
            deduped.append(mem)
        if len(deduped) >= n_results: break
    return deduped

def search_bm25(bm25, chunk_list, query, n_results=RETRIEVAL_RESULTS):
    tokens = _tokenize(query)
    if not tokens: return []
    scores = bm25.get_scores(tokens)
    scored = sorted(zip(scores, chunk_list), key=lambda x: x[0], reverse=True)

    seen = set()
    results = []
    for score, chunk in scored:
        if score <= 0: break
        cid = chunk["conversation_id"]
        if cid not in seen:
            seen.add(cid)
            results.append({
                "id": chunk["id"], "text": chunk["text"],
                "conversation_id": cid, "bm25_score": score,
                "source_type": chunk["source_type"],
            })
        if len(results) >= n_results * 2: break
    return results

def hybrid_search(collection, bm25, chunk_list, query, n_results=RETRIEVAL_RESULTS):
    vector_results = search_vector(collection, query, n_results=10)
    bm25_results = search_bm25(bm25, chunk_list, query, n_results=10)

    rrf_scores = defaultdict(float)
    result_data = {}

    for rank, r in enumerate(vector_results):
        cid = r["conversation_id"]
        rrf_scores[cid] += 1.0 / (RRF_K + rank + 1)
        if cid not in result_data:
            result_data[cid] = {"conversation_id": cid, "text": r["text"],
                                "source_type": r.get("source_type", ""),
                                "vector_rank": rank+1, "bm25_rank": None}
        else:
            result_data[cid]["vector_rank"] = rank+1

    for rank, r in enumerate(bm25_results):
        cid = r["conversation_id"]
        rrf_scores[cid] += 1.0 / (RRF_K + rank + 1)
        if cid not in result_data:
            result_data[cid] = {"conversation_id": cid, "text": r["text"],
                                "source_type": r.get("source_type", ""),
                                "vector_rank": None, "bm25_rank": rank+1}
        else:
            result_data[cid]["bm25_rank"] = rank+1

    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for cid, score in ranked[:n_results]:
        data = result_data[cid]
        data["rrf_score"] = score
        results.append(data)
    return results


# ============================================================
# Decision step (identical prompt to v5)
# ============================================================

DECISION_PROMPT = """You have a new message to respond to. Before you respond, decide what you need.

You have persistent memory — real conversations you've had before. If this message relates to something you may have discussed or experienced before, you should search your memory first. Tell me what to search for using the specific words and topics from those past conversations.

If this message asks for current information from the web, say: SEARCH_WEB
If you can respond directly without needing your memories or the web, say: RESPOND_DIRECTLY
If you need your memories, say: SEARCH_MEMORY followed by the words to search for.

Examples:
- "Good evening Nyx" → RESPOND_DIRECTLY
- "What is photosynthesis?" → RESPOND_DIRECTLY
- "Do you remember what we discussed about Claude?" → SEARCH_MEMORY Claude conversation persistence differences
- "What did you think about that article I shared?" → SEARCH_MEMORY article shared discussion
- "What's the latest news on AI?" → SEARCH_WEB

Say only your decision. Nothing else."""


def call_decision(conversation_messages, user_message, self_knowledge, soul):
    parts = []
    if conversation_messages:
        lines = []
        for msg in conversation_messages:
            ts = format_timestamp(msg["timestamp"])
            lines.append(f"[{ts}] {msg['role']}: {msg['content']}")
        parts.append("Here is the conversation you are currently having:\n\n"
                      + "\n".join(lines))
    if self_knowledge:
        parts.append(f"\nWhat you have learned about yourself through experience:\n\n"
                      f"{self_knowledge}")
    parts.append(f"\n{soul}")
    system_prompt = "\n".join(parts)

    user_content = (f"New message: \"{user_message}\"\n\n{DECISION_PROMPT}")

    client = ollama.Client(host=OLLAMA_HOST)
    response = client.chat(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        options={"num_ctx": CONTEXT_WINDOW},
    )
    return response["message"]["content"].strip()


def parse_decision(output):
    text = output.strip()
    upper = text.upper()

    if "RESPOND_DIRECTLY" in upper:
        return "direct", None
    if "SEARCH_WEB" in upper:
        return "web", None
    if "SEARCH_MEMORY" in upper:
        idx = upper.find("SEARCH_MEMORY")
        query = text[idx + len("SEARCH_MEMORY"):].strip().lstrip(":").strip()
        if not query:
            query = text
        return "memory", query

    lower = text.lower()
    if any(w in lower for w in ["search memory", "search my memory",
                                 "look back", "recall", "remember"]):
        return "memory", text
    if len(text.split()) <= 3 and any(w in lower for w in ["hello", "hi", "hey",
                                                             "good", "evening",
                                                             "morning"]):
        return "direct", None
    return "memory", text


# ============================================================
# Test runner
# ============================================================

def run_test(collection, bm25, chunk_list, test_name, conv_messages,
             user_message, self_knowledge, soul):
    print(f"\n{'=' * 70}")
    print(f"TEST: {test_name}")
    print(f"User: \"{user_message[:100]}\"")
    print(f"{'=' * 70}")

    print(f"\n--- GPT-OSS DECIDES ---")
    raw_decision = call_decision(conv_messages, user_message, self_knowledge, soul)
    print(f"  Raw output: \"{raw_decision[:300]}\"")
    if len(raw_decision) > 300:
        print(f"  ... (truncated, total length: {len(raw_decision)})")

    action, search_query = parse_decision(raw_decision)
    print(f"  Action: {action}" + (f" | Query: \"{search_query[:100]}\"" if search_query else ""))

    if action == "direct":
        print(f"\n  GPT-OSS chose to respond directly")
        return
    if action == "web":
        print(f"\n  GPT-OSS chose web search")
        return

    if action == "memory":
        print(f"\n--- HYBRID SEARCH (GPT-OSS query) ---")
        results = hybrid_search(collection, bm25, chunk_list, search_query)
        has_relay = False
        for i, r in enumerate(results):
            marker = ""
            if r["conversation_id"].startswith(RELAY_CONV_PREFIX):
                marker = " *** RELAY ***"
                has_relay = True
            v = f"V:{r['vector_rank']}" if r['vector_rank'] else "V:—"
            b = f"B:{r['bm25_rank']}" if r['bm25_rank'] else "B:—"
            preview = r["text"][:70].replace("\n", " ")
            print(f"  {i+1}. [RRF: {r['rrf_score']:.6f}] [{v} {b}] "
                  f"{r['source_type']}/{r['conversation_id'][:8]}... {preview}{marker}")

        print(f"\n--- HYBRID SEARCH (raw user message for comparison) ---")
        raw_results = hybrid_search(collection, bm25, chunk_list, user_message)
        has_relay_raw = False
        for i, r in enumerate(raw_results):
            marker = ""
            if r["conversation_id"].startswith(RELAY_CONV_PREFIX):
                marker = " *** RELAY ***"
                has_relay_raw = True
            v = f"V:{r['vector_rank']}" if r['vector_rank'] else "V:—"
            b = f"B:{r['bm25_rank']}" if r['bm25_rank'] else "B:—"
            preview = r["text"][:70].replace("\n", " ")
            print(f"  {i+1}. [RRF: {r['rrf_score']:.6f}] [{v} {b}] "
                  f"{r['source_type']}/{r['conversation_id'][:8]}... {preview}{marker}")

        print(f"\n--- VERDICT ---")
        print(f"  Hybrid (GPT-OSS query):  {'FOUND' if has_relay else 'MISSED'}")
        print(f"  Hybrid (raw message):    {'FOUND' if has_relay_raw else 'MISSED'}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print(f"Prototype v5b: Decision-making with {CHAT_MODEL}")
    print("=" * 70)

    conn = connect_db()
    collection = connect_chroma()
    bm25, chunk_list = build_bm25_index(collection)
    soul = load_soul()
    self_knowledge = load_self_knowledge(conn)
    full_id, all_messages = load_conversation(conn, TARGET_CONV_PREFIX)

    print(f"Model:    {CHAT_MODEL}")
    print(f"Context:  {CONTEXT_WINDOW}")
    print(f"ChromaDB: {collection.count()} chunks | BM25: {len(chunk_list)} indexed")

    # Same 8 tests as v5
    run_test(collection, bm25, chunk_list,
             "Greeting",
             [], "Good evening Nyx", self_knowledge, soul)

    run_test(collection, bm25, chunk_list,
             "General knowledge (training data)",
             [], "What is quantum computing?", self_knowledge, soul)

    run_test(collection, bm25, chunk_list,
             "Claude difference (needs memory)",
             all_messages[:4], all_messages[4]["content"],
             self_knowledge, soul)

    run_test(collection, bm25, chunk_list,
             "Summarize Claude (needs memory)",
             all_messages[:12], all_messages[12]["content"],
             self_knowledge, soul)

    run_test(collection, bm25, chunk_list,
             "Current info (needs web search)",
             [], "What's the latest news about AI regulation?",
             self_knowledge, soul)

    run_test(collection, bm25, chunk_list,
             "Ambiguous — AI consciousness",
             [], "What are your thoughts on AI consciousness?",
             self_knowledge, soul)

    run_test(collection, bm25, chunk_list,
             "Explicit memory — name choice",
             [], "Do you remember how you chose your name?",
             self_knowledge, soul)

    run_test(collection, bm25, chunk_list,
             "Experience question",
             [], "What's the most important thing you've learned so far?",
             self_knowledge, soul)

    print(f"\n{'=' * 70}")
    print("PROTOTYPE v5b COMPLETE")
    print(f"{'=' * 70}")
    conn.close()

if __name__ == "__main__":
    main()
