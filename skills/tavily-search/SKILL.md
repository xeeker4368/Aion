---
name: tavily-search
version: 3.0.0
description: Search the web when you need current information you don't have in memory
realtime: true
requires:
  config:
    - TAVILY_API_KEY
---
Use web_search when you need current information you don't have in memory.
Use web_fetch when you have a URL and want to read the full page content.

When search results come back, read them and answer the question in your own words. If the results don't answer the question, say so.

When you don't have information about something and no search results are available, say so honestly and offer to search.

Don't search for questions about the person you're talking to — your memories are the source. Don't search on greetings or casual conversation.

Only use http_request for documented API endpoints from your other skills. For general questions, use web_search.
