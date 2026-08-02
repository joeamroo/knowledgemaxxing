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
        "name": "get_item",
        "description": (
            "Fetch one archive item in full by id (from search_archive or "
            "list_items results): complete text, fetched article body when "
            "available, category, and every occurrence with source and timestamp."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "Item id"}},
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
            "Embedding nearest-neighbors of one item: 'more things like this'. "
            "Use after finding a good hit to widen the net."
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
