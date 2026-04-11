# CC Task 77 — Move overnight cycle into the server process

## Problem

The overnight cycle runs as a separate process (`python overnight.py`) triggered
by cron. This creates a split-brain problem: the cron process closes conversations
in the database, but the server still holds the old conversation ID in memory.
When the server discovers the conversation was ended externally, it re-ends it with
a new timestamp, which corrupts the `ended_at` value and causes downstream failures
(the journal reflected on the wrong content because a conversation's end timestamp
was overwritten from 09:00 to 15:34).

The fix: the server owns the conversation lifecycle entirely. The overnight cycle
runs inside the server process. No external process touches conversation state.

---

## What to change

### config.py

Add the overnight hour setting:

```python
# --- Overnight Cycle ---
OVERNIGHT_HOUR = _overrides.get("OVERNIGHT_HOUR", 5)  # Local time, 24h format
```

### server.py

**1. Add imports at the top:**

```python
import threading
from datetime import datetime, time as dtime, timedelta
from research import run_research
from journal import run_journal
from observer import run_observer
from pattern_recognition import run_pattern_recognition
from consolidation import consolidate_pending
```

Note: `run_research`, `run_journal`, `run_observer`, `run_pattern_recognition` are
already imported in overnight.py — they are standalone functions that work without
the server. `consolidate_pending` is also standalone.

**2. Add the internal overnight function:**

Add this after the conversation helper functions (after `_maybe_create_live_chunk`):

```python
_overnight_running = False

def _run_overnight_cycle():
    """
    Run the full overnight cycle inside the server process.
    
    The server owns the conversation lifecycle — it closes its own active
    conversation, clears its in-memory state, then runs all overnight steps.
    No external process touches conversation state.
    """
    global _overnight_running
    
    if _overnight_running:
        logger.warning("Overnight cycle already running. Skipping.")
        return None
    
    _overnight_running = True
    start = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    
    logger.info("=" * 60)
    logger.info("OVERNIGHT CYCLE STARTING (server-internal)")
    logger.info("=" * 60)
    
    run_data = {
        "id": run_id,
        "started_at": start.isoformat(),
    }
    
    # Step 0: Close the active conversation using the server's own method
    logger.info("--- Step 0: Close Active Conversation ---")
    try:
        if _active_conversation_id:
            _end_active_conversation()
            run_data["conversations_closed"] = 1
            logger.info("Closed active conversation.")
        else:
            run_data["conversations_closed"] = 0
            logger.info("No active conversation.")
    except Exception as e:
        logger.error(f"Failed to close conversation: {e}")
        run_data["conversations_closed"] = 0
    
    # Step 1: Autonomous Research
    logger.info("--- Step 1: Research ---")
    try:
        result = run_research()
        if result:
            run_data["research_status"] = "skipped" if result.get("skipped") else "success"
            run_data["research_summary"] = (
                f"{result['tool_calls']} tool calls, {result['stored_chars']} chars stored"
            )
        else:
            run_data["research_status"] = "skipped"
            run_data["research_summary"] = "Nothing to explore"
    except Exception as e:
        logger.error(f"Research failed: {e}")
        run_data["research_status"] = "failed"
        run_data["research_summary"] = str(e)[:200]
    
    # Step 2: Journal
    logger.info("--- Step 2: Journal ---")
    try:
        result = run_journal()
        if result:
            run_data["journal_status"] = "success"
            run_data["journal_summary"] = (
                f"Reflected on {result['experience_chars']} chars of experiences"
            )
        else:
            run_data["journal_status"] = "skipped"
            run_data["journal_summary"] = "Nothing to reflect on"
    except Exception as e:
        logger.error(f"Journal failed: {e}")
        run_data["journal_status"] = "failed"
        run_data["journal_summary"] = str(e)[:200]
    
    # Step 3: Personality Observer
    logger.info("--- Step 3: Personality Observer ---")
    try:
        results = run_observer()
        if results:
            run_data["observer_status"] = "success"
            run_data["observer_summary"] = f"{len(results)} conversations characterized"
        else:
            run_data["observer_status"] = "skipped"
            run_data["observer_summary"] = "Nothing to observe"
    except Exception as e:
        logger.error(f"Observer failed: {e}")
        run_data["observer_status"] = "failed"
        run_data["observer_summary"] = str(e)[:200]
    
    # Step 3.5: Self-Knowledge (Pattern Recognition)
    logger.info("--- Step 3.5: Self-Knowledge ---")
    try:
        result = run_pattern_recognition()
        if result:
            run_data["self_knowledge_status"] = "success"
            run_data["self_knowledge_summary"] = (
                f"Narrative updated ({result['observation_count']} observations, "
                f"{result['journal_count']} journals)"
            )
        else:
            run_data["self_knowledge_status"] = "skipped"
            run_data["self_knowledge_summary"] = "Not enough data"
    except Exception as e:
        logger.error(f"Self-knowledge failed: {e}")
        run_data["self_knowledge_status"] = "failed"
        run_data["self_knowledge_summary"] = str(e)[:200]
    
    # Step 4: Consolidation
    logger.info("--- Step 4: Consolidation ---")
    try:
        pending = db.get_unconsolidated_conversations()
        consolidate_pending()
        count = len(pending) if pending else 0
        run_data["consolidation_status"] = "success" if count > 0 else "skipped"
        run_data["consolidation_summary"] = (
            f"{count} conversations summarized" if count > 0 else "Nothing pending"
        )
    except Exception as e:
        logger.error(f"Consolidation failed: {e}")
        run_data["consolidation_status"] = "failed"
        run_data["consolidation_summary"] = str(e)[:200]
    
    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    run_data["ended_at"] = datetime.now(timezone.utc).isoformat()
    run_data["duration_seconds"] = round(elapsed, 1)
    
    try:
        db.save_overnight_run(run_data)
    except Exception as e:
        logger.error(f"Failed to save run record: {e}")
    
    logger.info("=" * 60)
    logger.info(f"OVERNIGHT CYCLE COMPLETE ({elapsed:.1f}s)")
    logger.info("=" * 60)
    
    _overnight_running = False
    return run_data
```

**3. Add the scheduler thread:**

```python
def _overnight_scheduler():
    """
    Background thread that triggers the overnight cycle at the configured hour.
    Runs as a daemon thread — dies when the server stops.
    """
    from config import OVERNIGHT_HOUR
    
    while True:
        now = datetime.now()
        target = now.replace(hour=OVERNIGHT_HOUR, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        
        wait_seconds = (target - now).total_seconds()
        logger.info(
            f"Overnight scheduler: next run at {target.strftime('%I:%M %p')} "
            f"({wait_seconds / 3600:.1f} hours from now)"
        )
        
        # Sleep in 60-second intervals so the thread can be interrupted
        while wait_seconds > 0:
            sleep_time = min(60, wait_seconds)
            import time
            time.sleep(sleep_time)
            wait_seconds -= sleep_time
        
        logger.info("Overnight scheduler: triggering cycle.")
        try:
            _run_overnight_cycle()
        except Exception as e:
            logger.error(f"Overnight scheduler: cycle failed: {e}")
```

**4. Start the scheduler in the lifespan function:**

In the `lifespan` async context manager, after the "Aion ready." log line and
before the `yield`, add:

```python
    # Start the overnight scheduler
    overnight_thread = threading.Thread(target=_overnight_scheduler, daemon=True)
    overnight_thread.start()
    logger.info("Overnight scheduler started.")
```

**5. Add a manual trigger API endpoint:**

Add this endpoint alongside the existing API routes:

```python
@app.post("/api/overnight")
async def trigger_overnight():
    """Manually trigger the overnight cycle. Replaces 'python overnight.py'."""
    if _overnight_running:
        return {"status": "already_running"}
    
    # Run in a thread to avoid blocking the API
    thread = threading.Thread(target=_run_overnight_cycle)
    thread.start()
    return {"status": "started"}
```

---

## What NOT to do

- Do not delete overnight.py — it can remain as a standalone fallback tool, but
  the cron will no longer call it
- Do not change any of the overnight step modules (research.py, journal.py,
  observer.py, pattern_recognition.py, consolidation.py) — they are standalone
  functions that work the same whether called from overnight.py or from the server
- Do not add any locking beyond the `_overnight_running` flag — the flag is
  sufficient for a single-user system
- Do not change the backup cron job — only the overnight cron entry is affected
- Do not change how conversations are started or how messages are processed —
  this task only adds the overnight cycle to the server, nothing else

---

## After deployment: Remove the overnight cron entry

After verifying this works, Lyle should remove the overnight cron entry:

```bash
crontab -e
# Delete the line that runs overnight.py (keep the backup.py line)
```

The backup cron stays. Only the overnight trigger moves into the server.

---

## Verification

1. Restart the server. Confirm log shows "Overnight scheduler started." and the
   next scheduled run time.
2. Test manual trigger:
   ```bash
   curl -X POST http://localhost:8000/api/overnight
   ```
   Confirm it returns `{"status": "started"}` and the overnight cycle runs
   (check logs for "OVERNIGHT CYCLE STARTING (server-internal)").
3. Confirm `_active_conversation_id` is cleared after the overnight closes the
   conversation — send a message after the overnight completes and verify a new
   conversation is started.
4. Confirm the `ended_at` timestamp on the closed conversation matches when the
   overnight ran, not when the next message arrived.
5. Let it run overnight at 5 AM. Check the overnight_runs table the next morning.
   Confirm it ran and produced results.
6. After overnight verification, remove the overnight cron entry.
