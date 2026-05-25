"""Collect Reddit threads about the Hormuz / jet fuel crisis.

Single-file, no shared infrastructure.  Mirrors the Assignment-1 style:

1. For each subreddit in ``config.SUBREDDITS``, search the project
   keywords with ``sort=top, t=year`` and keep the highest-scoring
   threads that fall inside the project date window.
2. For each chosen thread, fetch its public JSON listing and flatten
   the comment tree (no ``morechildren`` expansion — the listing
   already returns hundreds of top comments per thread).
3. Write everything to a single ``data/raw/reddit/reddit_hormuz_jetfuel_<UTC>.json``
   in the same shape used by Assignment 1's ``fetchYoutubeData.py``
   (one top-level object with ``parameters``, ``counts``, ``threads``).

Run with::

    python3 src/reddit_collect.py
    python3 src/reddit_collect.py --threads-per-sub 8
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from config import (
    PROJECT_END_DATE,
    PROJECT_START_DATE,
    RAW_REDDIT_DIR,
    REDDIT_QUERIES,
    REDDIT_THREADS_PER_SUB,
    SUBREDDITS,
    get_user_agent,
)


REDDIT_BASE = "https://www.reddit.com"
MORECHILDREN_URL = f"{REDDIT_BASE}/api/morechildren.json"
SLEEP_SECONDS = 3.0
MAX_RETRIES = 4
MAX_MORE_ROUNDS = 400  # safety cap on morechildren expansion per thread


# ---------------------------------------------------------------------------
# Minimal HTTP helper
# ---------------------------------------------------------------------------


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    session: requests.Session | None = None,
) -> Any:
    """GET a Reddit JSON endpoint with simple exponential backoff on 429/5xx."""
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", get_user_agent())
    for attempt in range(MAX_RETRIES + 1):
        time.sleep(SLEEP_SECONDS)
        try:
            r = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"  [warn] network error on {url}: {exc} (retry {attempt})")
            time.sleep(min(60, 2 ** attempt * 5))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            wait = min(60.0, float(r.headers.get("Retry-After", 2 ** attempt * 5)))
            print(f"  [warn] HTTP {r.status_code} on {url}; backing off {wait:.0f}s")
            time.sleep(wait)
            continue
        if not r.ok:
            print(f"  [warn] HTTP {r.status_code} on {url}; giving up")
            return None
        try:
            return r.json()
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _date_to_epoch(d) -> int:
    return int(datetime.combine(d, dtime.min, tzinfo=timezone.utc).timestamp())


START_EPOCH = _date_to_epoch(PROJECT_START_DATE)
END_EPOCH = _date_to_epoch(PROJECT_END_DATE) + 86_400 - 1  # inclusive


def discover_top_threads(
    subreddit: str,
    queries: Iterable[str],
    n_top: int,
    session: requests.Session,
) -> list[dict[str, Any]]:
    """Return the top-``n_top`` in-window threads in ``r/<subreddit>``.

    Unions ``sort=top, t=year`` results across the project keyword set,
    then keeps the highest-scoring threads whose ``created_utc`` falls
    inside the project window.  Each chosen thread also records which
    queries matched it and the score at discovery time (provenance for
    the report).
    """
    candidates: dict[str, dict[str, Any]] = {}
    matched_queries: dict[str, list[str]] = {}
    for query in queries:
        url = f"{REDDIT_BASE}/r/{subreddit}/search.json"
        data = get_json(
            url,
            params={
                "q": query,
                "restrict_sr": "on",
                "sort": "top",
                "t": "year",
                "limit": 100,
                "raw_json": 1,
            },
            session=session,
        )
        if not data:
            continue
        for child in data.get("data", {}).get("children", []):
            s = child.get("data", {}) or {}
            sid = s.get("id")
            created = float(s.get("created_utc") or 0)
            if not sid:
                continue
            if not (START_EPOCH <= created <= END_EPOCH):
                continue
            matched_queries.setdefault(sid, []).append(query)
            if sid in candidates:
                continue
            candidates[sid] = s
    # Attach provenance so we can quote the sampling frame in the report.
    for sid, s in candidates.items():
        s["matched_queries"] = matched_queries.get(sid, [])
        s["score_at_discovery"] = int(s.get("score") or 0)
    ranked = sorted(
        candidates.values(),
        key=lambda s: s["score_at_discovery"],
        reverse=True,
    )
    return ranked[:n_top]


# ---------------------------------------------------------------------------
# Thread hydration
# ---------------------------------------------------------------------------


def _build_comment_row(
    d: dict[str, Any],
    submission_id: str,
    parent_author: str | None,
    parent_kind: str,
    depth: int,
) -> dict[str, Any]:
    body = d.get("body") or ""
    author = d.get("author")
    return {
        "comment_id": d.get("id"),
        "name": d.get("name"),
        "submission_id": submission_id,
        "parent_id": d.get("parent_id"),
        "parent_kind": parent_kind,  # "submission" or "comment" — disambiguates the reply graph
        "author": author,
        "parent_author": parent_author,
        "body": body,
        "score": d.get("score"),
        "depth": depth,
        "created_utc": d.get("created_utc"),
        "is_submitter": d.get("is_submitter"),
        "distinguished": d.get("distinguished"),
        "controversiality": d.get("controversiality"),
        "removed": body in {"[removed]", "[deleted]"} or author == "[deleted]",
    }


def parse_thread_listing(
    listing: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Flatten a Reddit thread JSON listing.

    Returns ``(submission, comments, more_items)`` where ``more_items`` is
    the list of unresolved ``kind="more"`` placeholders (each with the
    children IDs and depth at which they were encountered).  Callers can
    feed ``more_items`` to :func:`expand_morechildren` to recover the
    rest of the comment tree.
    """
    head = (listing[0].get("data", {}) or {}).get("children", []) if listing else []
    if not head:
        raise ValueError("Empty Reddit thread listing")
    s = head[0].get("data", {}) or {}
    submission = {
        "submission_id": s.get("id"),
        "name": s.get("name"),
        "subreddit": s.get("subreddit"),
        "title": s.get("title", ""),
        "selftext": s.get("selftext", ""),
        "author": s.get("author"),
        "created_utc": s.get("created_utc"),
        "score": s.get("score"),
        "num_comments": s.get("num_comments"),
        "permalink": s.get("permalink"),
        "url": s.get("url"),
    }

    comments: list[dict[str, Any]] = []
    more_items: list[dict[str, Any]] = []

    def walk(children, parent_author: str | None, parent_kind: str, depth: int) -> None:
        for c in children:
            kind = c.get("kind")
            if kind == "more":
                md = c.get("data", {}) or {}
                more_items.append(
                    {
                        "parent_id": md.get("parent_id"),
                        "children": list(md.get("children") or []),
                        "count": md.get("count"),
                        "depth": depth,
                    }
                )
                continue
            if kind != "t1":
                continue
            d = c.get("data", {}) or {}
            comments.append(
                _build_comment_row(d, submission["submission_id"], parent_author, parent_kind, depth)
            )
            replies = d.get("replies")
            if isinstance(replies, dict):
                walk(
                    (replies.get("data", {}) or {}).get("children", []),
                    parent_author=d.get("author"),
                    parent_kind="comment",
                    depth=depth + 1,
                )

    if len(listing) > 1:
        walk(
            (listing[1].get("data", {}) or {}).get("children", []),
            parent_author=submission["author"],
            parent_kind="submission",
            depth=0,
        )
    return submission, comments, more_items


def expand_morechildren(
    submission: dict[str, Any],
    comments: list[dict[str, Any]],
    more_items: list[dict[str, Any]],
    session: requests.Session,
    max_rounds: int = MAX_MORE_ROUNDS,
) -> tuple[int, list[dict[str, Any]]]:
    """Expand ``kind="more"`` placeholders via ``api/morechildren.json``.

    Returns ``(new_comments_added, still_unresolved)``.  Updates
    ``comments`` in place.  ``more_items`` is consumed.
    """
    submission_name = submission.get("name") or f"t3_{submission.get('submission_id')}"
    seen_ids: set[str] = {c["comment_id"] for c in comments if c.get("comment_id")}
    id_to_author: dict[str, str | None] = {
        c["name"]: c.get("author") for c in comments if c.get("name")
    }
    queue = list(more_items)
    added = 0
    rounds = 0
    while queue and rounds < max_rounds:
        rounds += 1
        head = queue.pop(0)
        children_ids = head.get("children") or []
        if not children_ids:
            continue
        for start in range(0, len(children_ids), 100):
            chunk = children_ids[start : start + 100]
            payload = get_json(
                MORECHILDREN_URL,
                params={
                    "api_type": "json",
                    "link_id": submission_name,
                    "children": ",".join(chunk),
                    "limit_children": "false",
                    "sort": "old",
                    "raw_json": 1,
                },
                session=session,
            )
            if not payload:
                # API failure — re-queue so we can report it as unresolved.
                queue.append(head)
                return added, queue
            things = (
                ((payload or {}).get("json") or {}).get("data", {}).get("things", [])
            )
            for thing in things:
                kind = thing.get("kind")
                if kind == "more":
                    md = thing.get("data") or {}
                    queue.append(
                        {
                            "parent_id": md.get("parent_id"),
                            "children": list(md.get("children") or []),
                            "count": md.get("count"),
                            "depth": head["depth"] + 1,
                        }
                    )
                    continue
                if kind != "t1":
                    continue
                d = thing.get("data") or {}
                tid = d.get("id")
                if not tid or tid in seen_ids:
                    continue
                parent_id = d.get("parent_id")
                parent_author = id_to_author.get(parent_id) if parent_id else None
                parent_kind = "submission" if parent_id == submission_name else "comment"
                # Morechildren returns flat; depth is approximate (head depth or +1
                # when parent matches a comment we already have).
                depth = head["depth"] if parent_id == head.get("parent_id") else head["depth"] + 1
                row = _build_comment_row(
                    d, submission["submission_id"], parent_author, parent_kind, depth
                )
                comments.append(row)
                seen_ids.add(tid)
                if d.get("name"):
                    id_to_author[d["name"]] = d.get("author")
                added += 1
    return added, queue


def hydrate_thread(
    permalink: str,
    session: requests.Session,
    *,
    expand_more: bool = True,
) -> dict[str, Any] | None:
    """Fetch the thread, flatten it, and optionally expand morechildren.

    Returns the same payload shape regardless of expansion:
    ``{"submission", "comments", "unresolved_more_count", "more_expanded_count"}``.
    """
    url = f"{REDDIT_BASE}{permalink.rstrip('/')}.json"
    # Reddit's default sort is "best/confidence"; "old" gives chronological
    # which is more useful for diffusion analysis (cascade timing).
    listing = get_json(
        url, params={"raw_json": 1, "limit": 500, "sort": "old"}, session=session
    )
    if not listing:
        return None
    try:
        submission, comments, more_items = parse_thread_listing(listing)
    except ValueError:
        return None

    expanded = 0
    if expand_more and more_items:
        expanded, more_items = expand_morechildren(submission, comments, more_items, session)

    unresolved = sum(len(m.get("children") or []) for m in more_items)
    return {
        "submission": submission,
        "comments": comments,
        "more_expanded_count": expanded,
        "unresolved_more_count": unresolved,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def fetch_reddit_data(
    *,
    subreddits: tuple[str, ...] = SUBREDDITS,
    queries: tuple[str, ...] = REDDIT_QUERIES,
    threads_per_sub: int = REDDIT_THREADS_PER_SUB,
    out_path: Path,
) -> dict[str, Any]:
    """Run the full discover-then-hydrate sweep and write a single JSON file."""
    session = requests.Session()
    session.headers.update({"User-Agent": get_user_agent()})

    out_path.parent.mkdir(parents=True, exist_ok=True)

    threads: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    expanded_total = 0
    unresolved_total = 0

    for sub in subreddits:
        print(f"\n=== r/{sub} ===")
        candidates = discover_top_threads(sub, queries, threads_per_sub, session)
        print(f"  {len(candidates)} top threads selected")
        for s in candidates:
            sid = s.get("id")
            score = s.get("score")
            n_comments = s.get("num_comments")
            title = (s.get("title") or "")[:80]
            print(f"    {sid} score={score} num_comments={n_comments}  {title}")
            permalink = s.get("permalink") or f"/r/{sub}/comments/{sid}/"
            t = hydrate_thread(permalink, session)
            if t is None:
                print(f"    [skip] failed to hydrate {sid}")
                continue
            # Attach the discovery provenance for the report's sampling-frame audit.
            t["discovery"] = {
                "matched_queries": s.get("matched_queries", []),
                "score_at_discovery": s.get("score_at_discovery"),
            }
            threads.append(t)
            counts[sub] = counts.get(sub, 0) + len(t["comments"])
            expanded_total += t.get("more_expanded_count", 0)
            unresolved_total += t.get("unresolved_more_count", 0)
            print(
                f"      -> {len(t['comments'])} comments "
                f"(expanded {t.get('more_expanded_count', 0)}, "
                f"unresolved {t.get('unresolved_more_count', 0)})"
            )

    total = sum(len(t["comments"]) for t in threads)
    print(
        f"\nDone: {len(threads)} threads, {total:,} comments. "
        f"Per-sub counts: {counts}"
    )

    payload = {
        "collected_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "Reddit public thread JSON (www.reddit.com)",
        "parameters": {
            "subreddits": list(subreddits),
            "queries": list(queries),
            "threads_per_sub": threads_per_sub,
            "project_window": [PROJECT_START_DATE.isoformat(), PROJECT_END_DATE.isoformat()],
            "sleep_seconds": SLEEP_SECONDS,
            "max_retries": MAX_RETRIES,
            "thread_comment_sort": "old",
            "morechildren_expansion": True,
            "max_morechildren_rounds_per_thread": MAX_MORE_ROUNDS,
        },
        "counts": {
            "threads": len(threads),
            "comments_total": total,
            "comments_per_subreddit": counts,
            "morechildren_expanded_total": expanded_total,
            "morechildren_unresolved_total": unresolved_total,
        },
        "threads": threads,
    }
    # Atomic write: build a .tmp then rename so a crash mid-write never leaves
    # an unparseable JSON file alongside a successful one.
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    tmp_path.replace(out_path)
    print(f"Wrote {out_path}")
    return payload


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subreddits", nargs="+", default=list(SUBREDDITS))
    parser.add_argument("--queries", nargs="+", default=list(REDDIT_QUERIES))
    parser.add_argument("--threads-per-sub", type=int, default=REDDIT_THREADS_PER_SUB)
    parser.add_argument(
        "--out",
        type=Path,
        default=RAW_REDDIT_DIR / f"reddit_hormuz_jetfuel_{_utc_stamp()}.json",
    )
    args = parser.parse_args()
    fetch_reddit_data(
        subreddits=tuple(args.subreddits),
        queries=tuple(args.queries),
        threads_per_sub=args.threads_per_sub,
        out_path=args.out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
