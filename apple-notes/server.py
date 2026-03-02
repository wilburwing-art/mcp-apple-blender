"""Apple Notes MCP Server — read/write access via osascript."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser

from fastmcp import FastMCP

mcp = FastMCP("apple-notes")

# ---------------------------------------------------------------------------
# HTML → plain text converter for Apple Notes body HTML
# ---------------------------------------------------------------------------

class NotesHTMLToText(HTMLParser):
    """Minimal HTML-to-text converter for Apple Notes body content."""

    BLOCK_TAGS = {"div", "p", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._in_pre = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")
        if tag == "pre":
            self._in_pre = True
        if tag == "li":
            self._parts.append("- ")
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self._parts.append(f"[")

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self._in_pre = False
        if tag == "a":
            self._parts.append("]")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        # Collapse runs of blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text(html: str) -> str:
    parser = NotesHTMLToText()
    parser.feed(html)
    return parser.get_text()


# ---------------------------------------------------------------------------
# AppleScript helpers
# ---------------------------------------------------------------------------

def run_applescript(script: str, timeout: int = 30) -> str:
    """Run an AppleScript via osascript, returning stdout."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not allowed assistive access" in stderr or "not permitted" in stderr:
            raise RuntimeError(
                "macOS Automation permission denied. Grant Terminal (or your IDE) "
                "access in System Settings → Privacy & Security → Automation."
            )
        raise RuntimeError(f"AppleScript error: {stderr}")
    return result.stdout.strip()


def escape_for_applescript(s: str) -> str:
    r"""Escape a string for embedding in AppleScript double-quoted literals.

    AppleScript has no backslash escapes. To embed a double-quote, you break
    out of the string and concatenate `quote`: "before" & quote & "after"
    Backslashes are literal in AppleScript strings, so no escaping needed.
    """
    return s.replace('"', '" & quote & "')


# Date formats observed from AppleScript on macOS (locale-dependent)
_DATE_FORMATS = [
    "%A, %B %d, %Y at %I:%M:%S %p",   # English (US): "Monday, January 6, 2025 at 10:30:00 AM"
    "%A, %d %B %Y at %H:%M:%S",        # English (UK): "Monday, 6 January 2025 at 10:30:00"
    "%Y-%m-%d %H:%M:%S",               # ISO-ish
    "%m/%d/%Y %I:%M:%S %p",            # Short US
    "%d/%m/%Y %H:%M:%S",               # Short UK
    "%B %d, %Y at %I:%M:%S %p",        # Without weekday
    "%d %B %Y at %H:%M:%S",            # Without weekday UK
]


def parse_applescript_date(s: str) -> datetime:
    """Parse an AppleScript date string, trying multiple locale formats."""
    s = s.strip()
    # Normalize Unicode non-breaking spaces and trim
    s = s.replace("\u202f", " ").replace("\u00a0", " ")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Last resort: try dateutil if available, otherwise return epoch
    try:
        from dateutil.parser import parse as dateutil_parse
        return dateutil_parse(s)
    except Exception:
        return datetime(1970, 1, 1)


def extract_hashtags(text: str) -> list[str]:
    """Extract #hashtags from text (used on note names)."""
    return re.findall(r"#(\w+)", text)


# ---------------------------------------------------------------------------
# Metadata cache
# ---------------------------------------------------------------------------

@dataclass
class NoteMetadata:
    note_id: str
    name: str
    folder: str
    creation_date: datetime
    modification_date: datetime

    @property
    def hashtags(self) -> list[str]:
        return extract_hashtags(self.name)


@dataclass
class _MetadataCache:
    notes: list[NoteMetadata] = field(default_factory=list)
    _last_refresh: float = 0.0
    _ttl: float = 60.0

    @property
    def is_stale(self) -> bool:
        return (time.time() - self._last_refresh) > self._ttl

    def refresh(self) -> None:
        """Fetch all note metadata by iterating folders (avoids -1728 bug).

        Uses batch property access (e.g. `id of notes of folder`) which is
        orders of magnitude faster than per-note loops for large collections.
        """
        # Get folder list
        folder_script = """
            tell application "Notes"
                set folderNames to {}
                repeat with f in folders
                    set end of folderNames to name of f
                end repeat
                return folderNames
            end tell
        """
        raw_folders = run_applescript(folder_script)
        folders = [f.strip() for f in raw_folders.split(",") if f.strip()]

        all_notes: list[NoteMetadata] = []
        for folder_name in folders:
            escaped_folder = escape_for_applescript(folder_name)
            # Batch-fetch all four properties, serialize each as LF-delimited,
            # separated by a ###SECTION### marker line.
            note_script = f"""
                tell application "Notes"
                    set theFolder to folder "{escaped_folder}"
                    set noteIds to id of notes of theFolder
                    set noteNames to name of notes of theFolder
                    set noteCreated to creation date of notes of theFolder
                    set noteMod to modification date of notes of theFolder

                    set createdStrs to {{}}
                    repeat with d in noteCreated
                        set end of createdStrs to (d as string)
                    end repeat
                    set modStrs to {{}}
                    repeat with d in noteMod
                        set end of modStrs to (d as string)
                    end repeat

                    set lf to ASCII character 10
                    set AppleScript's text item delimiters to lf
                    set r1 to noteIds as string
                    set r2 to noteNames as string
                    set r3 to createdStrs as string
                    set r4 to modStrs as string
                    set AppleScript's text item delimiters to ""

                    return r1 & lf & "###SECTION###" & lf & r2 & lf & "###SECTION###" & lf & r3 & lf & "###SECTION###" & lf & r4
                end tell
            """
            raw = run_applescript(note_script, timeout=60)
            if not raw:
                continue
            sections = raw.split("###SECTION###")
            if len(sections) < 4:
                continue
            ids = [x for x in sections[0].strip().split("\n") if x]
            names = [x for x in sections[1].strip().split("\n") if x]
            created_strs = [x for x in sections[2].strip().split("\n") if x]
            mod_strs = [x for x in sections[3].strip().split("\n") if x]

            count = min(len(ids), len(names), len(created_strs), len(mod_strs))
            for i in range(count):
                all_notes.append(NoteMetadata(
                    note_id=ids[i],
                    name=names[i],
                    folder=folder_name,
                    creation_date=parse_applescript_date(created_strs[i]),
                    modification_date=parse_applescript_date(mod_strs[i]),
                ))

        self.notes = all_notes
        self._last_refresh = time.time()

    def get(self, force_refresh: bool = False) -> list[NoteMetadata]:
        if force_refresh or self.is_stale:
            self.refresh()
        return self.notes


_cache = _MetadataCache()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_notes(folder: str | None = None, hashtag: str | None = None) -> list[dict]:
    """List notes with optional filtering by folder or hashtag.

    Args:
        folder: Filter to notes in this folder name.
        hashtag: Filter to notes containing this hashtag (without #).
    """
    notes = _cache.get()
    if folder:
        notes = [n for n in notes if n.folder.lower() == folder.lower()]
    if hashtag:
        tag = hashtag.lstrip("#").lower()
        notes = [n for n in notes if tag in [t.lower() for t in n.hashtags]]
    return [
        {
            "id": n.note_id,
            "name": n.name,
            "folder": n.folder,
            "created": n.creation_date.isoformat(),
            "modified": n.modification_date.isoformat(),
            "hashtags": n.hashtags,
        }
        for n in notes
    ]


def _fetch_note_body(note_id: str) -> str:
    """Fetch the HTML body of a single note by ID, return as plain text."""
    escaped_id = escape_for_applescript(note_id)
    script = f"""
        tell application "Notes"
            set theNote to note id "{escaped_id}"
            return body of theNote
        end tell
    """
    html = run_applescript(script)
    return html_to_text(html)


def _find_note(name: str | None = None, note_id: str | None = None) -> NoteMetadata:
    """Resolve a note by name or ID."""
    if not name and not note_id:
        raise ValueError("Provide either 'name' or 'note_id'.")
    notes = _cache.get()
    if note_id:
        matches = [n for n in notes if n.note_id == note_id]
    else:
        assert name is not None
        lower_name = name.lower()
        # Exact match first
        matches = [n for n in notes if n.name.lower() == lower_name]
        if not matches:
            # Substring match
            matches = [n for n in notes if lower_name in n.name.lower()]
    if not matches:
        raise ValueError(f"No note found matching: {name or note_id}")
    if len(matches) > 1 and not note_id:
        names = [f"  - {m.name} (folder: {m.folder}, id: {m.note_id})" for m in matches[:10]]
        raise ValueError(
            f"Multiple notes match '{name}'. Be more specific or use note_id:\n"
            + "\n".join(names)
        )
    return matches[0]


@mcp.tool()
def get_note(name: str | None = None, note_id: str | None = None) -> dict:
    """Get the full content of a note by name or ID.

    Args:
        name: Note title (exact or substring match). Use if you don't have the ID.
        note_id: Apple Notes internal ID (e.g. "x-coredata://..."). Preferred for precision.
    """
    meta = _find_note(name=name, note_id=note_id)
    body = _fetch_note_body(meta.note_id)
    return {
        "id": meta.note_id,
        "name": meta.name,
        "folder": meta.folder,
        "created": meta.creation_date.isoformat(),
        "modified": meta.modification_date.isoformat(),
        "hashtags": meta.hashtags,
        "content": body,
    }


@mcp.tool()
def search_notes(
    query: str,
    folder: str | None = None,
    search_body: bool = False,
    limit: int = 20,
) -> list[dict]:
    """Search notes by name (fast) or body content (slower).

    Args:
        query: Search string (case-insensitive).
        folder: Restrict search to this folder.
        search_body: If True, also search note body content (fetches each note individually, slower).
        limit: Max results to return.
    """
    notes = _cache.get()
    if folder:
        notes = [n for n in notes if n.folder.lower() == folder.lower()]

    lower_q = query.lower()

    # Name matches (fast, from cache)
    name_matches = [n for n in notes if lower_q in n.name.lower()]

    if search_body:
        # Also search body content for notes not already matched
        matched_ids = {n.note_id for n in name_matches}
        remaining = [n for n in notes if n.note_id not in matched_ids]
        body_matches = []
        for n in remaining:
            if len(name_matches) + len(body_matches) >= limit:
                break
            try:
                body = _fetch_note_body(n.note_id)
                if lower_q in body.lower():
                    body_matches.append(n)
            except Exception:
                continue
        all_matches = name_matches + body_matches
    else:
        all_matches = name_matches

    return [
        {
            "id": n.note_id,
            "name": n.name,
            "folder": n.folder,
            "created": n.creation_date.isoformat(),
            "modified": n.modification_date.isoformat(),
            "match_type": "name" if lower_q in n.name.lower() else "body",
        }
        for n in all_matches[:limit]
    ]


@mcp.tool()
def create_note(title: str, body: str, folder: str | None = None) -> dict:
    """Create a new note in Apple Notes.

    Args:
        title: Note title.
        body: Note body as plain text. Newlines become <br> in the note.
        folder: Target folder name. Defaults to "Notes".
    """
    target_folder = folder or "Notes"
    escaped_title = escape_for_applescript(title)
    # Convert plain text body to HTML
    html_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html_body = html_body.replace("\n", "<br>")
    escaped_body = escape_for_applescript(html_body)
    escaped_folder = escape_for_applescript(target_folder)

    script = f"""
        tell application "Notes"
            set theFolder to folder "{escaped_folder}"
            set noteBody to "<html><head><title>{escaped_title}</title></head><body><h1>{escaped_title}</h1>{escaped_body}</body></html>"
            set newNote to make new note at theFolder with properties {{body:noteBody}}
            return id of newNote
        end tell
    """
    new_id = run_applescript(script)
    # Invalidate cache so the new note shows up
    _cache._last_refresh = 0.0
    return {
        "id": new_id.strip(),
        "name": title,
        "folder": target_folder,
        "status": "created",
    }


@mcp.tool()
def append_to_note(
    content: str,
    name: str | None = None,
    note_id: str | None = None,
) -> dict:
    """Append text to an existing note.

    Args:
        content: Plain text to append. Newlines become <br>.
        name: Note title to find. Use if you don't have the ID.
        note_id: Apple Notes internal ID. Preferred for precision.
    """
    meta = _find_note(name=name, note_id=note_id)
    escaped_id = escape_for_applescript(meta.note_id)
    html_content = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html_content = html_content.replace("\n", "<br>")
    escaped_content = escape_for_applescript(html_content)

    script = f"""
        tell application "Notes"
            set theNote to note id "{escaped_id}"
            set currentBody to body of theNote
            set newBody to currentBody & "<br>{escaped_content}"
            set body of theNote to newBody
            return "ok"
        end tell
    """
    run_applescript(script)
    _cache._last_refresh = 0.0
    return {
        "id": meta.note_id,
        "name": meta.name,
        "folder": meta.folder,
        "status": "appended",
    }


@mcp.tool()
def get_changed_notes(since: str, folder: str | None = None) -> list[dict]:
    """Get notes modified after a given timestamp.

    Args:
        since: ISO 8601 timestamp (e.g. "2025-01-06T10:00:00"). Notes modified after this time are returned.
        folder: Restrict to this folder.
    """
    try:
        cutoff = datetime.fromisoformat(since)
    except ValueError:
        raise ValueError(f"Invalid ISO timestamp: {since}")

    # Make cutoff offset-naive for comparison with AppleScript dates (which are naive)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.replace(tzinfo=None)

    # Force cache refresh to get latest modification dates
    notes = _cache.get(force_refresh=True)
    if folder:
        notes = [n for n in notes if n.folder.lower() == folder.lower()]

    changed = [n for n in notes if n.modification_date > cutoff]
    changed.sort(key=lambda n: n.modification_date, reverse=True)

    return [
        {
            "id": n.note_id,
            "name": n.name,
            "folder": n.folder,
            "modified": n.modification_date.isoformat(),
        }
        for n in changed
    ]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
