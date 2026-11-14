---
name: view
description: Opens the tapestry viewer, a read-only page in the browser where the user can see their memories, topics, associations and evidence, and try what recall would bring to mind. Use when the user asks to see, browse or look through their memory or memory graph.
allowed-tools: Bash(*tapestry* view --detach*)
---

!`"${CLAUDE_PLUGIN_ROOT}/bin/tapestry" --plugin-data "${CLAUDE_PLUGIN_DATA}" view --detach 2>&1`

Above is what starting the viewer printed. If it gives an address, the page is opening in the user's browser: tell them the address in one line, and that it stops by itself once unused. If it says why the viewer couldn't start, tell them that, in plain words, and what it says to do.

The viewer only looks. If the user wants something corrected or forgotten, they ask you, and you use your tapestry tools as usual.
