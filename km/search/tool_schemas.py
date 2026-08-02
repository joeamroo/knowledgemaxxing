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
]
