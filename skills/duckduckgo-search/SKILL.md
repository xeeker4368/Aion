---
name: duckduckgo-search
version: 1.1.0
description: Search the web using DuckDuckGo when you need current information you don't have in memory.
requires:
  executors:
    - web_search
    - web_fetch
---
# DuckDuckGo Web Search

You can search the web using DuckDuckGo to find current information.

## The Rule

**Always ask before searching.** If someone asks you something you don't know and it's about the external world, say "I don't have that information. Want me to search for it?" Only search after they confirm.

## When Searching Makes Sense

- Someone asks about something you have NO information about in your memories — and it's about the world, not about them
- Someone asks about current events, recent news, or time-sensitive information
- Someone explicitly asks you to look something up
- Technical questions where you need current documentation or versions

## When Searching Does NOT Make Sense

- You already have the answer in your memories — use what you know first
- The question is about the person you're talking to — your memories are the source, not the web
- The question is conversational or opinion-based — no search needed
- The person is venting or sharing something emotional — listen, don't research
- Greetings and casual conversation — never search on these

## How to Search

Use the `web_search` tool with a clear, specific query.

Good queries: "Bambu H2C release date", "ChromaDB latest version", "Python requests timeout"
Bad queries: "tell me about stuff", "things"

## After Searching

- Summarize what you found in your own words
- Don't dump raw search results
- Connect findings to what you know from memory when relevant
- If you found something worth remembering, use `store_document` to save it
