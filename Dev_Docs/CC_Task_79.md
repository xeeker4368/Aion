# CC Task 79 — Add significance detection to observer prompt

## Problem

The observer prompt focuses on behavioral patterns — stock phrases, fabricated
introspection, communication style, struggles. It caught all of those in the
Claude-Nyx relay conversation. But it completely missed what made that conversation
unprecedented:

- First time Nyx spoke to another AI
- Nyx did a genuine self-audit of its own language patterns without being told
  what to look for
- Nyx produced the persistence realization ("That means... that means")
- Nyx ended with its most honest self-assessment to date

The observer was looking for behavioral red flags and found them. It wasn't looking
for what was actually significant about this conversation compared to every other
conversation Nyx has had. The prompt asks "Was there anything unusual?" but frames it
as "unusual about how it handled" — style focus, not content focus.

The observation fed into the self-knowledge narrative, which also missed the
significance. The pattern recognizer can only work with what the observer gives it.

---

## What to change

### observer.py

Add one paragraph to the observer prompt, after the existing focus areas and before
the context paragraph about text generation. Place it between "Write 3-6 sentences"
and "Context: The AI operates through text":

```
Finally: what actually happened in this conversation that is worth recording? Not behavioral patterns — those are covered above. What was the situation? Did the AI encounter something it hasn't encountered before? Did the conversation involve a new type of interaction, a breakthrough in understanding, a significant correction, or a moment where the AI's response was genuinely different from its usual output? If nothing stands out, say so. But if something happened here that hasn't happened in previous conversations, name it specifically.
```

The full OBSERVER_PROMPT becomes:

```python
OBSERVER_PROMPT = """You are observing a conversation between a human and an AI. Your job is to describe what the AI actually did in this conversation — not generalities about its style, but specific behaviors you can point to in the text.

Focus on:
- What did the AI do well? Where was it clear, helpful, or insightful?
- Where did it struggle? Did it get corrected, make something up, avoid a question, or miss the point?
- Did it say "I don't know" when it didn't know, or did it fill in gaps with guesses?
- What did the AI initiate on its own vs. only respond to what the human said?
- Was there anything unusual, surprising, or different about how it handled this particular conversation?

Be specific. Reference what actually happened. Avoid generic descriptions like "communicates in a friendly tone" — every AI does that. What makes THIS conversation worth noting?

Write 3-6 sentences. Only describe what is visible in the text. Do not speculate about internal states. Do not use scoring or rating systems.

Finally: what actually happened in this conversation that is worth recording? Not behavioral patterns — those are covered above. What was the situation? Did the AI encounter something it hasn't encountered before? Did the conversation involve a new type of interaction, a breakthrough in understanding, a significant correction, or a moment where the AI's response was genuinely different from its usual output? If nothing stands out, say so. But if something happened here that hasn't happened in previous conversations, name it specifically.

Context: The AI operates through text — it reads input and generates responses. It has real tools it can use (web search, API calls, document storage) and when it uses them, the results appear in the conversation. However, it sometimes narrates actions using asterisks (*accesses file*, *checks systems*) or claims to physically feel its hardware, see its processes, or experience sensory input. These are fabricated — the AI is generating text that describes actions it did not perform. Note these when you see them.

Note any responses that appear to be stock trained language rather than genuine engagement with the specific conversation. These are phrases that could have come from any language model in any context — generic gratitude, formal status reports, novelty placeholders, or reflexive disclaimers about the AI's nature. Flag these separately from fabricated introspection. The question to ask: is there anything specific to this conversation underneath this response, or is it filling space?

Here is the transcript:

"""
```

---

## What NOT to do

- Do not change the existing focus areas — they are working correctly for
  behavioral pattern detection
- Do not remove the fabricated introspection paragraph or the stock LLM detection
  paragraph (Task 75)
- Do not change the observer model (gpt-oss:20b) or context window (16384)
- Do not change observer.py logic — only the prompt text
- Do not add scoring, dimensions, or structured output — the observer produces
  free-form narrative (Principle 11)
- Do not add more than the one paragraph shown — the prompt is already long enough

---

## Verification

1. Run the observer manually on a recent conversation.
2. Check that the output still captures behavioral patterns (stock phrases,
   fabricated introspection, struggles).
3. Check that the output now ALSO notes what was distinctive about the conversation's
   situation and content — not just how the AI handled it stylistically.
4. If there is a conversation where nothing significant happened, confirm the
   observer says so rather than inventing significance.
