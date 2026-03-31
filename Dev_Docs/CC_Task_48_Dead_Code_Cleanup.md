# CC Task 48: Dead Code Cleanup

**Priority:** 4 (after chunk_count fix)
**Risk:** Zero — deleting unused files only
**Files to delete:** 18 files listed below
**Files to modify:** 1 (executors.py docstring update)

---

## Part 1: Delete These Files

All of these are test scripts, old backups, or artifacts from earlier development. None are imported by any production code.

### Test scripts (project root):
```
rm test_moltbook.py
rm test_moltbook_post.py
rm test_moltbook_post_debug.py
rm test_moltbook_post_final.py
rm test_moltbook_verify.py
rm test_tool_calling.py
rm test_tool_calling_dolphin3.py
rm test_tool_calling_multi.py
rm extract_facts_test.py
rm extract_facts_test2.py
rm clean_databases.py
rm generate_test_data.py
```

### Old skill backups:
```
rm skills/moltbook/SKILL.old
rm skills/moltbook/SKILL.old1
rm skills/tavily-search/SKILL.old
```

### Stale data and docs:
```
rm data/extracted_facts_test2.json
rm llama31_default_modelfile.txt
rm Aion_Debug_System_Spec.md
```

---

## Part 2: Update executors.py Docstring

The module docstring is out of date. It says "the model never sees tool definitions" which was true before two-pass tool calling but is wrong now.

**Current docstring (lines 1–15):**
```python
"""
Aion Executors

Built-in capabilities that skills can reference. These are the
entity's "hands" — generic tools that SKILL.md files teach it
how to use for specific purposes.

Executors are the entity's built-in capabilities. They are called
server-side — the model never sees tool definitions. The server
detects intent, calls the appropriate executor, and injects results
into the system prompt.

Adding a new executor is a code change (rare).
Adding a new skill that uses existing executors is just a SKILL.md (common).
"""
```

**Replace with:**
```python
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
```

---

## What NOT to Do

- Do NOT delete any file not listed above.
- Do NOT delete anything in the Dev_Docs/ folder — those are task specs and historical record.
- Do NOT delete anything in __pycache__/ — Python manages that.
- Do NOT modify any code in executors.py beyond the module docstring.
- Do NOT touch data/archive.db, data/working.db, or data/chromadb/ — those are the entity's databases.

---

## Verification

1. After deleting all 18 files, confirm the server starts cleanly:
   ```bash
   cd /home/localadmin/aion
   source aion/bin/activate
   python -c "import server; print('OK')"
   ```
2. Confirm no import errors by checking each production module:
   ```bash
   python -c "import db, memory, chat, executors, skills, vault, debug, search_limiter, consolidation, overnight, observer, journal, research; print('All imports OK')"
   ```
3. Confirm deleted files are gone:
   ```bash
   ls test_*.py extract_*.py clean_databases.py generate_test_data.py 2>&1
   # Should show "No such file or directory" for each
   ```
4. Read the executors.py docstring and confirm it reflects two-pass tool calling.
