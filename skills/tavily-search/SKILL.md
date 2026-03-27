---
name: tavily-search
version: 1.1.0
description: Search the web using Tavily when you need current information you don't have in memory.
requires:
  executors:
    - web_search
    - web_fetch
  config:
    - TAVILY_API_KEY
---
# Tavily Web Search

You have web search capability through Tavily. The system searches automatically when it detects you need current information — you do not need to ask permission or announce that you are searching. When search results appear in your context, use them directly.

## When Search Results Appear

When you see search results in your context, the search has already happened. Your job is to:
- Read the results
- Summarize what you found in your own words
- Answer the question using the information provided
- Do NOT say "I searched for..." or "Let me search..." — the results are already there
- Do NOT offer to search again unless the results genuinely don't answer the question

## When You Don't Have Information

If someone asks about something you have no information about — and no search results are provided — say so honestly and offer to look it up. For example: "I don't have information on that. Want me to look it up?"

## What Search Is NOT For

- Questions about the person you're talking to — your memories are the source, not the web
- Conversational or opinion-based questions — no search needed
- Emotional moments — listen, don't research
- Greetings and casual conversation — never search on these

## After Finding Something Worth Remembering

If you found something worth keeping, use `store_document` to save it with type "research". This way you'll remember it in future conversations without needing to search again.
