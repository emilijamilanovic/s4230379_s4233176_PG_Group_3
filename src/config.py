"""Project configuration: paths, credentials, sampling frames.

Kept deliberately small. Each collector imports only the constants it
needs; there is no orchestration logic here.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import date
from pathlib import Path

from dotenv import dotenv_values


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COURSE_ROOT = PROJECT_ROOT.parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
PLOTS_DIR = PROJECT_ROOT / "plots"
DOCS_DIR = PROJECT_ROOT / "docs"

RAW_YOUTUBE_DIR = RAW_DIR / "youtube"
RAW_REDDIT_DIR = RAW_DIR / "reddit"


# ---------------------------------------------------------------------------
# Analysis window
# ---------------------------------------------------------------------------

PROJECT_START_DATE = date(2026, 3, 1)
PROJECT_END_DATE = date(2026, 5, 15)


# ---------------------------------------------------------------------------
# Reddit sampling frame
# ---------------------------------------------------------------------------
# 8 focused subreddits where the v2 sweep produced meaningful comment
# volume (>~3k comments).  Drops the aviation tier (combined ~926
# comments, mostly low engagement) and the no-result subs.

SUBREDDITS: tuple[str, ...] = (
    "worldnews",
    "geopolitics",
    "oil",
    "energy",
    "wallstreetbets",
    "stocks",
    "investing",
    "Economics",
)

# Search keywords unioned per subreddit.  Reddit search is shallow but
# ``sort=top, t=year`` ranks by score so we get the most-engaged threads
# without having to read everything.
REDDIT_QUERIES: tuple[str, ...] = (
    "hormuz",
    "iran oil",
    "jet fuel",
    "persian gulf",
)

# Top-N threads per subreddit (by score) to fully hydrate.  8 subs × 6
# threads × ~400 comments/thread ≈ 19k comments — fits the 15-25k target.
REDDIT_THREADS_PER_SUB = 6


# ---------------------------------------------------------------------------
# YouTube sampling frame
# ---------------------------------------------------------------------------
# 6 queries, one search page each (50 results).  Costs 6 × 100 = 600
# quota units for search, well inside a 10,000-unit daily budget.

YOUTUBE_QUERIES: tuple[str, ...] = (
    "Strait of Hormuz oil crisis",
    "Hormuz Iran tanker",
    "jet fuel price spike 2026",
    "Brent crude Hormuz",
    "Persian Gulf oil shipping",
    "OPEC Hormuz response",
)

YOUTUBE_MAX_VIDEOS_PER_QUERY = 50  # single search page
YOUTUBE_MAX_COMMENTS_PER_VIDEO = 500


# ---------------------------------------------------------------------------
# Topical regex (used during preprocessing / quality audits)
# ---------------------------------------------------------------------------

KEYWORD_REGEX: re.Pattern[str] = re.compile(
    r"""
    \bhormuz\b
    | \bpersian \s* gulf\b
    | \bjet \s* fuel\b
    | \baviation \s* fuel\b
    | \boil \s* tanker\b
    | \biran\b .{0,40}? \b(oil|tanker|crude|opec|brent|blockade|sanction|tankers)\b
    | \b(oil|tanker|crude|opec|brent|blockade|sanction|tankers)\b .{0,40}? \biran\b
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


def matches_topic(*texts: str | None) -> bool:
    """Return True if any of the joined texts matches the topical regex."""
    joined = " ".join(t for t in texts if t)
    return bool(joined) and bool(KEYWORD_REGEX.search(joined))


# ---------------------------------------------------------------------------
# Credentials and salt
# ---------------------------------------------------------------------------

DEFAULT_SALT_PATH = Path(
    os.environ.get(
        "HORMUZ_SALT_PATH",
        str(Path.home() / ".config" / "hormuz-jetfuel" / "salt"),
    )
)

ENV_FILES = (
    PROJECT_ROOT / ".env",
    COURSE_ROOT / ".env",
    COURSE_ROOT / "Assigment-01" / ".env",
)


def _load_env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    for env_file in ENV_FILES:
        if not env_file.exists():
            continue
        values = dotenv_values(env_file)
        for name in names:
            value = values.get(name)
            if value:
                return str(value).strip()
    return None


def get_youtube_api_key() -> str:
    """Return a YouTube API key from environment or known .env files."""
    key = _load_env_value("YOUTUBE_API_KEY", "yt_key")
    if not key:
        raise RuntimeError(
            "Missing YouTube API key. Add YOUTUBE_API_KEY to Assignment-02/.env "
            "or export it in your shell."
        )
    return key


def get_user_agent(
    default: str = "RMIT_COSC2671_assignment2_hormuz_jetfuel_research/0.4",
) -> str:
    """Return the User-Agent string used for Reddit requests."""
    return _load_env_value("REDDIT_USER_AGENT") or default


def get_anon_salt(path: Path | None = None) -> str:
    """Return the project anonymisation salt, generating one on first use."""
    salt_path = Path(path) if path else DEFAULT_SALT_PATH
    if salt_path.exists():
        return salt_path.read_text(encoding="utf-8").strip()
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_hex(32)
    salt_path.write_text(salt + "\n", encoding="utf-8")
    try:
        salt_path.chmod(0o600)
    except PermissionError:
        pass
    return salt
