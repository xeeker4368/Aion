---
name: tavily-search
version: 2.0.1
description: Search the web when you need current information you don't have in memory
realtime: true
requires:
  executors:
    - web_search
    - web_fetch
  config:
    - TAVILY_API_KEY
tools:
  - name: web_search
    description: Search the web for current information. Use when someone asks about something you don't know or need up-to-date data on.
    executor: web_search
    parameters:
      query:
        type: string
        description: What to search for
        required: true
  - name: web_fetch
    description: Fetch the full content of a web page by URL. Use when you have a URL and want to read what's on the page.
    executor: web_fetch
    parameters:
      url:
        type: string
        description: The URL to fetch
        required: true
---
When search results come back, read them and answer the question in your own words. If the results don't answer the question, say so.

When you don't have information about something and no search results are available, say so honestly and offer to search.

Don't search for questions about the person you're talking to — your memories are the source. Don't search on greetings or casual conversation.
