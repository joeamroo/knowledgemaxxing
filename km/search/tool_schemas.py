"""Tool schemas for the archive tools, in Anthropic tool-use format.

The MCP server renames input_schema -> inputSchema; the archivist agent
passes these straight to the API. One source of truth for both.
"""

TOOL_SCHEMAS = [
    {
        "name": "search_archive",
        "description": (
            "Hybrid semantic + keyword search over the user's whole digital "
            "history: browser visits, bookmarks, tweets, saves, AI chats, and "
            "fetched article text. Returns items with the exact matching "
            "passage when one exists, plus provenance (which source saw it, "
            "when). Best for half-remembered passages: describe the memory in "
            "natural language. Supports operators in the query: site:, kind:, "
            "cat:, source:, before:/after:<YYYY[-MM[-DD]]>."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language description or keywords"},
                "k": {"type": "integer", "description": "Max results (default 10)"},
                "essays_only": {"type": "boolean", "description": "Only long-form articles/essays"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "deep_search",
        "description": (
            "The heavy retrieval tool for finding things BY MEANING in a huge "
            "corpus: runs several query phrasings through all retrieval legs "
            "with candidate pools of hundreds, then a local cross-encoder "
            "rereads each (query, passage) pair and rescores. Use this "
            "(not search_archive) when the user half-remembers an essay or "
            "idea, when a first search missed, or for 'all my essays about X'. "
            "Slower than search_archive but far higher recall and precision; "
            "costs no API money. Returns items with passages, best first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Rich natural-language description of the meaning sought"},
                "k": {"type": "integer", "description": "Max results (default 25)"},
                "essays_only": {"type": "boolean", "description": "Long-form articles only"},
                "kind": {"type": "string"},
                "domain": {"type": "string"},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "map_topics",
        "description": (
            "Semantic overview instead of a search: clusters the matching "
            "slice of the corpus (or all essays when no query) into labeled "
            "topic groups with exemplar items. Use for 'what do I have "
            "about X', 'what are my essays actually about', or to orient "
            "before a deep dive. All local."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional focus, e.g. 'artificial intelligence'"},
                "essays_only": {"type": "boolean", "description": "Default true"},
                "n_clusters": {"type": "integer", "description": "Topic groups (default 6)"},
                "sample": {"type": "integer", "description": "Items to cluster (default 400)"},
            },
        },
    },
    {
        "name": "get_item",
        "description": (
            "Fetch one archive item in full by id (from search_archive or "
            "list_items results): complete text, fetched article body when "
            "available, category, and every occurrence with source and timestamp."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Item id"},
                "offset": {"type": "integer", "description": "Character offset into long text fields (default 0)"},
                "max_chars": {"type": "integer", "description": "Window size per text field (default 12000); the result reports chars remaining"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "get_items",
        "description": (
            "Bulk fetch: full text of up to 50 items in one call, any kind. "
            "After rostering tweets/notes/saves with list_items or a search, "
            "pull all their texts at once instead of one get_item per item."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "Item ids (max 50)"},
                "max_chars_each": {"type": "integer", "description": "Per-item text cap (default 2000)"},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "period_summary",
        "description": (
            "One-call orientation for a date window across EVERY artifact "
            "kind: counts by kind and month, top domains and categories, all "
            "search queries and chat conversations in the window (with ids). "
            "The starting move for 'what was going on with me between X and "
            "Y' before drilling in with list_items / get_chat_messages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "ISO date, inclusive"},
                "date_to": {"type": "string", "description": "ISO date, inclusive"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "find_episodes",
        "description": (
            "Stitch browsing visits into rabbit-hole episodes: sessions of "
            "many visits with no long gaps, named by dominant domains, with "
            "sample titles and item ids. The unit for 'what was I doing that "
            "night' and 'my deep-dive sessions about X last month'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "ISO date lower bound"},
                "date_to": {"type": "string", "description": "ISO date upper bound"},
                "gap_minutes": {"type": "integer", "description": "Gap that ends a session (default 45)"},
                "min_items": {"type": "integer", "description": "Minimum visits to count (default 8)"},
                "limit": {"type": "integer", "description": "Max episodes (default 10)"},
            },
        },
    },
    {
        "name": "get_chat_messages",
        "description": (
            "Structured messages of one AI chat conversation (ChatGPT/Claude "
            "logs, kind=chat_conversation). role='user' returns exactly what "
            "the person asked in that conversation, which is the tool for "
            "'what did I ask about X' retrospectives. Far cheaper than "
            "get_item on long transcripts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "chat_conversation item id (from list_items or search)"},
                "role": {"type": "string", "description": "user | assistant; omit for the full transcript"},
                "max_messages": {"type": "integer", "description": "Cap (default 100)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "list_items",
        "description": (
            "Filtered listing without a search query: the tool for building "
            "lists ('all my starred essays from 2023', 'everything I saved "
            "from gwern.net'). Filters combine with AND. Returns compact "
            "rows: id, title, url, domain, first_seen, interest score."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "visit | bookmark | like | retweet | bookmark_tweet | note | chat_conversation | favorite | upvote | search_query"},
                "category": {"type": "string", "description": "Tweet/content category slug (e.g. aphorism, tool_or_resource)"},
                "domain": {"type": "string", "description": "Exact domain (e.g. paulgraham.com)"},
                "source": {"type": "string", "description": "Source kind substring (e.g. twitter, reddit, chrome)"},
                "date_from": {"type": "string", "description": "ISO date lower bound"},
                "date_to": {"type": "string", "description": "ISO date upper bound"},
                "starred": {"type": "boolean"},
                "is_essay": {"type": "boolean", "description": "Long-form articles only"},
                "in_reading_list": {"type": "boolean"},
                "sort": {"type": "string", "description": "recent | oldest | interest (default recent)"},
                "limit": {"type": "integer", "description": "Max rows (default 50, cap 200)"},
            },
        },
    },
    {
        "name": "archive_stats",
        "description": (
            "Scale and shape of the archive: total items, counts per source "
            "and kind, top domains, tweet categories. Use to orient before "
            "broad questions."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "similar_items",
        "description": (
            "Related items for one anchor via three fused signals: same "
            "meaning (embeddings), shared language (distinctive terms), and "
            "read together (same browsing session). Each result carries "
            "'reasons'. Use after a good hit to widen the net."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Anchor item id"},
                "k": {"type": "integer", "description": "How many neighbors (default 8)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "star_item",
        "description": "Star (or unstar) an item so it shows in the Starred collection.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "starred": {"type": "boolean", "description": "false to unstar (default true)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "add_note",
        "description": "Attach a margin note to an item (replaces any existing note).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "note": {"type": "string"}},
            "required": ["id", "note"],
        },
    },
    {
        "name": "set_category",
        "description": "Recategorize an item (user override beats the classifier).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "category": {"type": "string"}},
            "required": ["id", "category"],
        },
    },
    {
        "name": "create_task",
        "description": "Add a task to the user's task list (e.g. 'Read: <title>').",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "due": {"type": "string", "description": "Optional ISO date"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a task done by its id (see get_tasks).",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "integer"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "get_tasks",
        "description": "The user's task list. status: open (default) | done | dropped.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
        },
    },
    {
        "name": "queue_reading",
        "description": "Put an item into today's reading feed ('read later').",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "get_reading_feed",
        "description": "Today's reading feed with read/unread state.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_collection",
        "description": (
            "Save a search or filter set as a named smart collection in the "
            "UI sidebar, so a list the user asked for stays one click away."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "query": {"type": "string", "description": "Optional search text"},
                "kind": {"type": "string"},
                "category": {"type": "string"},
                "domain": {"type": "string"},
                "is_essay": {"type": "boolean"},
                "date_from": {"type": "string"},
                "date_to": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "export_list",
        "description": (
            "Write a markdown file of links (exports/lists/<slug>.md) from "
            "explicit item ids, for lists the user wants as a file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "item_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["title", "item_ids"],
        },
    },
    {
        "name": "fetch_page",
        "description": (
            "Fetch one item's article text from the web right now (when "
            "get_item shows no article_body). Stores it for future search too."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
]
