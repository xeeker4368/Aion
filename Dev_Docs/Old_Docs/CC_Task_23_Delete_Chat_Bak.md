# CC Task 23: Delete Stale chat.bak

## Overview

`chat.bak` exists in the project root. It's a backup of `chat.py` from before the Session 8 rewrite. `chat.py` is the canonical file and has been verified working. The backup serves no purpose.

## The Fix

```bash
rm chat.bak
```

## How to Verify

```bash
ls chat.bak
# Should return: No such file or directory

ls chat.py
# Should exist
```
