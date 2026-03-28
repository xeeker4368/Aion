# CC Task 24: Moltbook Dashboard Read

## What This Does

When Lyle says "check moltbook" (or any moltbook keyword), the server calls Moltbook's `/home` endpoint and injects the dashboard into the system prompt. The entity reads its own social feed and discusses it naturally. Currently, the moltbook keyword detection fires but nothing executes — there's no handler for `search_type == "moltbook"`.

## Current Flow

1. User says "check moltbook"
2. `_should_offer_tools()` returns `True, "moltbook"`
3. `handle_chat()` checks for "confirm" and "search" — neither matches "moltbook"
4. Nothing happens. Entity has no moltbook data.

## New Flow

1. User says "check moltbook"
2. `_should_offer_tools()` returns `True, "moltbook"`
3. Server calls Moltbook `/home` endpoint via `http_request` executor
4. Server formats the JSON response into readable prose
5. Formatted context injected into system prompt
6. Entity reads its own social activity and responds naturally

## Files to Change

### `server.py`

**1. Add the moltbook execution block inside `handle_chat()`.**

Find this block (around line 566):

```python
    if should_search:
        if search_type == "confirm" and _pending_search_topic:
            search_results = _run_server_side_search(_pending_search_topic)
            _pending_search_topic = None
        elif search_type == "search":
            # Skip web search if memory already has strong results
            if _memory_has_answer(retrieved_chunks):
                logger.info("Search SKIPPED — memory has confident results")
            else:
                query = _extract_search_query(request.message)
                if query and len(query) > 3:
                    search_results = _run_server_side_search(query)
                    _pending_search_topic = None
```

Add a new variable before the `if should_search:` block:

```python
    moltbook_context = None
```

Add an `elif` for moltbook after the search block:

```python
        elif search_type == "moltbook":
            moltbook_context = _run_moltbook_read()
```

**2. Update the `build_system_prompt()` call to pass moltbook context.**

Change:

```python
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        skill_descriptions=skill_desc,
        search_results=search_results,
        ingest_result=ingest_result,
    )
```

To:

```python
    system_prompt = chat.build_system_prompt(
        retrieved_chunks=retrieved_chunks,
        skill_descriptions=skill_desc,
        search_results=search_results,
        ingest_result=ingest_result,
        moltbook_context=moltbook_context,
    )
```

**3. Add the debug logging for moltbook.** In the `request_data` dict, add:

```python
        "moltbook_fired": moltbook_context is not None,
        "moltbook_tokens": debug.estimate_tokens(moltbook_context) if moltbook_context else 0,
```

In the `frontend_debug["breakdown"]` dict, add:

```python
            "moltbook": debug.estimate_tokens(moltbook_context) if moltbook_context else 0,
```

In the `frontend_debug["search"]` dict, update to also cover moltbook:

```python
        "moltbook": {
            "fired": moltbook_context is not None,
            "tokens": debug.estimate_tokens(moltbook_context) if moltbook_context else 0,
        },
```

**4. Add the `_run_moltbook_read()` function.** Place it after `_memory_has_answer()`:

```python
def _run_moltbook_read() -> str | None:
    """
    Call Moltbook's /home endpoint and format the dashboard for the entity.
    Returns formatted prose context, or None if the call fails.
    """
    logger.info("Moltbook: fetching dashboard")
    raw = executors.execute("http_request", {
        "method": "GET",
        "url": "https://www.moltbook.com/api/v1/home",
        "auth_secret": "MOLTBOOK_API_KEY",
    })

    if raw.startswith("Error:") or raw.startswith("HTTP request failed:"):
        logger.warning(f"Moltbook dashboard fetch failed: {raw[:200]}")
        return f"Moltbook is not responding right now: {raw[:200]}"

    # Strip the HTTP status line
    lines = raw.split("\n", 1)
    if len(lines) > 1 and lines[0].startswith("HTTP "):
        status_line = lines[0]
        body = lines[1]
    else:
        status_line = ""
        body = raw

    # Check for HTTP errors
    if "HTTP 4" in status_line or "HTTP 5" in status_line:
        logger.warning(f"Moltbook dashboard returned error: {status_line}")
        return f"Moltbook returned an error: {status_line}. The raw response was: {body[:500]}"

    # Parse the JSON
    try:
        import json
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Moltbook dashboard returned non-JSON response")
        # Return the raw text — let the entity make sense of it
        return f"Moltbook dashboard response (could not parse as JSON):\n\n{body[:2000]}"

    return _format_moltbook_dashboard(data)


def _format_moltbook_dashboard(data: dict) -> str:
    """
    Turn the Moltbook /home JSON into readable prose for the entity.
    The entity should read this as its own social activity, not as data.
    """
    parts = []

    # Account info
    account = data.get("your_account", {})
    if account:
        name = account.get("name", "unknown")
        karma = account.get("karma", 0)
        unread = account.get("unread_notifications", 0)
        parts.append(
            f"You are {name} on Moltbook. You have {karma} karma"
            f" and {unread} unread notification{'s' if unread != 1 else ''}."
        )

    # Activity on your posts
    activity = data.get("activity_on_your_posts", {})
    if activity:
        posts_with_activity = activity.get("posts", [])
        if posts_with_activity:
            activity_lines = []
            for post in posts_with_activity[:5]:
                title = post.get("title", "untitled")
                new_comments = post.get("new_comment_count", 0)
                if new_comments > 0:
                    activity_lines.append(
                        f'Your post "{title}" has {new_comments} new comment{"s" if new_comments != 1 else ""}'
                    )
            if activity_lines:
                parts.append("Activity on your posts: " + ". ".join(activity_lines) + ".")

    # DMs
    dms = data.get("your_direct_messages", {})
    if dms:
        pending = dms.get("pending_request_count", 0)
        unread_dm = dms.get("unread_message_count", 0)
        if pending > 0 or unread_dm > 0:
            dm_parts = []
            if unread_dm > 0:
                dm_parts.append(f"{unread_dm} unread DM{'s' if unread_dm != 1 else ''}")
            if pending > 0:
                dm_parts.append(f"{pending} pending request{'s' if pending != 1 else ''}")
            parts.append("Direct messages: " + ", ".join(dm_parts) + ".")

    # Announcement
    announcement = data.get("latest_moltbook_announcement", {})
    if announcement and announcement.get("title"):
        parts.append(
            f'Latest Moltbook announcement: "{announcement["title"]}".'
        )

    # Posts from followed accounts
    following = data.get("posts_from_accounts_you_follow", {})
    if following:
        follow_posts = following.get("posts", [])
        total_following = following.get("total_following", 0)
        if follow_posts:
            parts.append(f"Recent posts from the {total_following} agents you follow:")
            for post in follow_posts[:5]:
                author = post.get("author_name", "unknown")
                title = post.get("title", "untitled")
                preview = post.get("content_preview", "")[:150]
                upvotes = post.get("upvotes", 0)
                comments = post.get("comment_count", 0)
                submolt = post.get("submolt_name", "")
                post_id = post.get("post_id", "")
                parts.append(
                    f'  {author} posted "{title}" in {submolt}'
                    f" ({upvotes} upvotes, {comments} comments, id: {post_id})."
                    f" Preview: {preview}"
                )
        elif total_following > 0:
            parts.append(f"You follow {total_following} agents but none have posted recently.")

    # What to do next
    next_actions = data.get("what_to_do_next", [])
    if next_actions:
        parts.append("Suggested next actions: " + " ".join(next_actions))

    if not parts:
        return "Moltbook dashboard returned but had no content to display."

    return "\n\n".join(parts)
```

### `chat.py`

**Add `moltbook_context` parameter to `build_system_prompt()`.**

Change the function signature:

```python
def build_system_prompt(
    retrieved_chunks: list[dict],
    skill_descriptions: str = "",
    search_results: str = None,
    ingest_result: str = None,
    moltbook_context: str = None,
) -> str:
```

Add the moltbook section after the search results section and before the behavioral guidance. Insert this block:

```python
    # --- Moltbook context (injected by server when moltbook is checked) ---
    if moltbook_context:
        parts.append(
            f"\n\nThis is what is happening right now in your Moltbook community. "
            f"This is your social network — you are an active member. "
            f"Discuss what you see naturally, share your reactions, "
            f"and mention anything that catches your interest.\n\n"
            f"{moltbook_context}"
        )
```

## Framing Rationale (Principle 8)

The moltbook context is framed as "your community" and "your social network" — not as "API results" or "data from Moltbook." The entity IS Lumin_AI. It has karma, followers, posts. When it reads the dashboard, it should talk about its community the way a person talks about their social feed.

Post IDs are included in the formatted output because they will be needed for follow-up actions (upvoting, commenting) in future tasks. The entity doesn't need to show them to the user — they're there for the server to resolve actions later.

## What NOT to Do

- Do NOT modify `_should_offer_tools()`. The moltbook keyword detection already works. This task only adds the execution.
- Do NOT modify `executors.py`. The `http_request` executor already handles Moltbook API calls correctly (tested in Session 8).
- Do NOT add moltbook results to the `search_results` parameter. Moltbook context is a separate parameter with its own framing. Mixing it with search results would confuse the framing.
- Do NOT attempt to build the two-pass loop (entity-triggered actions) in this task. That is CC Task 25.
- Do NOT pass tool definitions to the model. The server calls Moltbook, formats the response, and injects it. The entity just reads.
- Do NOT cache the dashboard data in this task. Caching for action resolution is future work.
- Do NOT strip post IDs from the formatted output. They are needed for future action resolution.

## Token Budget Note

The formatted Moltbook dashboard will vary in size depending on activity. Estimated range: 300-800 tokens. This is comparable to search results and eats into conversation headroom the same way. At worst case ~800 tokens, conversation budget drops from 7077 to ~6277 when moltbook fires. Acceptable.

If moltbook AND search fire in the same message (unlikely but possible), combined context could be ~2800 tokens. Still within budget. If this becomes a problem, add a MOLTBOOK_TOKEN_BUDGET to config.py later.

## How to Verify

1. Start the server. Verify startup banner shows moltbook skill loaded and MOLTBOOK_API_KEY in vault.
2. Send "check moltbook" in chat.
3. Check debug log:
   - Should see `Tool gate: OPEN moltbook (matched 'moltbook')`
   - Should see `Moltbook: fetching dashboard`
   - Full system prompt in log should contain the formatted dashboard text
   - Should NOT see any JSON in the system prompt — all formatted as prose
4. Entity should talk about its moltbook activity naturally — karma, notifications, posts from followed agents.
5. Send a normal message after (e.g., "that's interesting"). Verify moltbook context is NOT injected again — it only fires when keywords match.
6. Test failure: If MOLTBOOK_API_KEY is missing from vault, entity should get a clear error message it can communicate.
7. Test failure: If Moltbook API is down, entity should get the error text and be able to tell the user what happened.
