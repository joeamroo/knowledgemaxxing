export const CATEGORIES = [
  "anecdote", "interesting_fact", "thread", "joke", "link_to_essay", "quote",
  "tool_or_resource", "hot_take", "contrarian", "list", "aphorism",
  "natural_law", "personal", "other",
] as const;

export const CATEGORY_LABELS: Record<string, string> = {
  anecdote: "anecdote",
  interesting_fact: "fact",
  thread: "thread",
  joke: "joke",
  link_to_essay: "essay link",
  quote: "quote",
  tool_or_resource: "tool",
  hot_take: "hot take",
  contrarian: "contrarian",
  list: "list",
  aphorism: "aphorism",
  natural_law: "natural law",
  personal: "personal",
  other: "other",
};

export const catStyle = (category: string) =>
  ({ "--cat": `var(--cat-${category}, var(--ink-dim))` }) as React.CSSProperties;

export const KIND_LABELS: Record<string, string> = {
  like: "liked",
  retweet: "retweet",
  own_tweet: "my tweet",
  bookmark_tweet: "x bookmark",
  visit: "visit",
  bookmark: "bookmark",
  saved_post: "saved",
  saved_comment: "comment",
  favorite: "hn fav",
  upvote: "hn upvote",
  chat_conversation: "chat",
  chat_message: "chat link",
  search_query: "search",
  note: "note",
  linked: "discovered",
};

export const KIND_GLYPHS: Record<string, string> = {
  like: "♥",
  retweet: "⇄",
  own_tweet: "✎",
  bookmark_tweet: "⚑",
  visit: "·",
  bookmark: "❧",
  saved_post: "❧",
  saved_comment: "❝",
  favorite: "★",
  upvote: "▲",
  chat_conversation: "☰",
  chat_message: "☍",
  search_query: "?",
  note: "✎",
  linked: "⛓",
};

export const SOURCE_LABELS: Record<string, string> = {
  chrome_export: "chrome",
  chrome_live_history: "chrome live",
  chrome_bookmarks: "bookmarks",
  takeout_browser: "takeout",
  my_activity: "activity",
  my_activity_html: "activity",
  twitter_archive: "twitter",
  chat_export: "ai chats",
  reddit_gdpr: "reddit",
  reddit_saved: "reddit",
  substack_saved: "substack",
  hn: "hacker news",
  x_bookmarks: "x bookmarks",
  bookmarks_html: "bookmarks",
  onetab: "onetab",
  instapaper: "instapaper",
  pocket: "pocket",
  pocket_csv: "pocket",
  generic: "misc",
};
