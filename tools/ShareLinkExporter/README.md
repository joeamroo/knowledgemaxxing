# ShareLinkExporter

Exports every conversation from a ChatGPT iOS account you can no longer
log into elsewhere, by driving the ChatGPT app through iPhone Mirroring
and generating a Share Link per conversation. Output: links.csv,
links.json, log.txt, an archived HTML copy of every shared page, and a
parsed Markdown version of every conversation.

## Why this design

- iPhone Mirroring renders your phone as a video stream. macOS sees ONE
  window with no accessibility elements inside it, so element-based
  automation (AXUIElement, AppleScript UI scripting) cannot see the
  Share button. Coordinate clicking is the only way in today. The engine
  talks to an abstract InteractionDriver, so if Apple ever ships a real
  automation API the coordinate driver can be swapped without rewriting
  the app.
- Universal Clipboard is the completion signal. When the phone copies
  the share link, it appears on the Mac clipboard; the app polls
  NSPasteboard.changeCount and matches chatgpt.com/share URLs. No blind
  sleeps for the critical step.
- Every captured link is verified with a real HTTP request (expects
  2xx). A link that does not resolve is regenerated automatically.
- Two navigation modes:
  - scroll (default): opens each conversation, shares from the header
    controls (fixed position), taps Back, and pages through the sidebar.
    Chats are left exactly as they were.
  - archive (optional optimization): archives each chat after export so
    the next one is always the top row and no scrolling is needed.
    Chats are NOT deleted (Settings > Archived chats), but validate the
    behavior on your first conversation before trusting it.
- After the run, "Fetch titles + archive pages" downloads each public
  share page, saves the raw HTML to output/pages/, extracts the title,
  and parses the conversation into clean Markdown with User / ChatGPT
  speaker labels at output/markdown/. Message bodies come from the JSON
  embedded in the page, so code blocks, lists, and tables survive
  verbatim; if parsing fails for a page, a plain-text fallback is
  written and the raw HTML remains for a future parser pass.

## Requirements

- macOS Sequoia or later, Mac signed into the same Apple ID as the phone
- iPhone Mirroring working
- Handoff enabled on BOTH devices (General > AirPlay & Handoff) so
  Universal Clipboard works. Test it first: copy text on the phone,
  paste on the Mac. If that fails, fix it before running.
- ChatGPT iOS app logged in, sidebar listing your conversations

## Build and run

```bash
cd tools/ShareLinkExporter
swift run ShareLinkExporter
```

## Permissions (one-time)

1. Run once; macOS prompts for Accessibility access (or add manually in
   System Settings > Privacy & Security > Accessibility). If launching
   from a terminal, grant the permission to the TERMINAL app.
2. Relaunch after granting. No Screen Recording permission is needed.

## Walkthrough

1. Phone side ready: ChatGPT open, sidebar visible. Do not move or
   resize the iPhone Mirroring window after calibrating.
2. Run Calibration. For each prompt: press "Capture in 3s", hover the
   target inside the mirroring window, hold still. Steps marked
   optional (a separate Share menu item, a second Copy-link tap) can be
   skipped if your UI does not have them.
3. Press "Simulate clicks": a click-through overlay draws every planned
   click, numbered in execution order, on top of the mirroring window.
   Check each marker sits on its control. Fix any single target via
   "Edit targets" (per-target recapture, no full redo).
4. Enable Dry run and press Start once; it logs every action it would
   take. Then disable dry run.
5. Do one supervised conversation (Start, watch, Pause). Check
   links.csv got a verified URL and the app returned to the list (and
   archived the chat, if in archive mode). Resume and let it run. Do
   not use the Mac or copy anything while it runs.
6. When finished, press "Fetch titles + archive pages".

Output in ~/Documents/ShareLinkExporter/output/:
links.csv, links.json, log.txt, state.json, config.json,
pages/*.html, markdown/*.md

## Reliability behavior

- Clipboard-driven completion (25s timeout scaled by the Pace slider),
  HTTP verification of every link, 3 attempts per conversation with
  dismiss-and-recover between attempts, failures logged and skipped.
- Progress saves after every conversation; quitting or crashing loses
  nothing. Relaunch and Start to resume; "Retry N failures" re-runs
  only the failed ones.
- Duplicate share URLs are detected and marked.
- Pace slider multiplies all delays; raise it on a slow phone/network.

## Assumptions to validate on your first run

- Long-press on a sidebar row shows a menu with Share (and Archive):
  labels and positions vary by app version; that is what calibration
  and the optional steps absorb.
- One tap vs two taps to create-and-copy the link: calibrate the
  optional copy_link_confirm step if your UI needs the second tap.
- Archive removes the chat from the sidebar without deleting it and
  without killing its share link (archive mode only).
- Some conversations may refuse to generate share links; they will be
  recorded as failures, not crash the run.
- The share page embeds conversation JSON that the Markdown parser can
  read; the first fetch run reports how many pages parsed structurally.

## Known limitations

- Moving/resizing the mirroring window after calibration breaks
  coordinates; use Edit targets or recalibrate.
- Notifications popping over the phone UI can eat a tap; retries
  usually recover.
- In scroll mode, row-position math can drift on very long lists if
  iOS rubber-bands a scroll; if the overlay shows taps landing between
  rows after many pages, pause and nudge the list to realign.
- Share links exclude images/files in conversations; text only.
