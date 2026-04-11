# CC Task 75 — Add stock LLM detection to observer prompt

## What to change

In `observer.py`, add one paragraph to the existing observer prompt. Find the paragraph that instructs the observer about fabricated actions and asterisk roleplay. Add the following as a new paragraph immediately after it:

```
Note any responses that appear to be stock trained language rather than genuine engagement with the specific conversation. These are phrases that could have come from any language model in any context — generic gratitude, formal status reports, novelty placeholders, or reflexive disclaimers about the AI's nature. Flag these separately from fabricated introspection. The question to ask: is there anything specific to this conversation underneath this response, or is it filling space?
```

---

## What NOT to do

- Do not change any other part of the observer prompt
- Do not change the observer's output format — it still produces free-form narrative
- Do not add structured categories or scoring
- Do not modify pattern_recognition.py, overnight.py, or any other file
- Do not change the observer minimum message threshold or any config values

---

## Verification

After the next overnight cycle runs, check the new observations in working.db:

```sql
SELECT content FROM observations ORDER BY created_at DESC LIMIT 3;
```

At least one observation should include language about generic or stock responses if any were present in the evaluated transcripts. If tonight's conversations contained stock LLM phrases, the observer should flag them.
