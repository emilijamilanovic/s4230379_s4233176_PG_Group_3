"""Collect YouTube videos + comments about the Hormuz / jet fuel crisis.

Single-file, modelled on Assignment 1's ``fetchYoutubeData.py``:

    search → stats → comments → write JSON

A small ``QuotaTracker`` charges every API call against a daily 10,000-
unit budget and stops cleanly if the budget would be breached.  At the
defaults (6 queries × 1 search page, 500 comments per video cap) the
plan costs ~2,000 units — comfortably under the daily quota.

Run with::

    python3 src/youtube_collect.py
    python3 src/youtube_collect.py --max-comments-per-video 1000
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    PROJECT_END_DATE,
    PROJECT_START_DATE,
    RAW_YOUTUBE_DIR,
    YOUTUBE_MAX_COMMENTS_PER_VIDEO,
    YOUTUBE_MAX_VIDEOS_PER_QUERY,
    YOUTUBE_QUERIES,
    get_youtube_api_key,
)


# YouTube Data API v3 quota costs (units per call).
COST_SEARCH = 100
COST_VIDEOS = 1
COST_COMMENT_THREADS = 1
COST_COMMENTS = 1

DEFAULT_DAILY_QUOTA = 10_000
DEFAULT_SAFETY_BUFFER = 500


class QuotaTracker:
    """Tiny daily-quota gate. Charges before each API call."""

    def __init__(self, daily_limit: int = DEFAULT_DAILY_QUOTA,
                 safety_buffer: int = DEFAULT_SAFETY_BUFFER) -> None:
        self.daily_limit = daily_limit
        self.safety_buffer = safety_buffer
        self.spent = 0

    @property
    def usable(self) -> int:
        return max(0, self.daily_limit - self.safety_buffer)

    @property
    def remaining(self) -> int:
        return max(0, self.usable - self.spent)

    def can_afford(self, cost: int) -> bool:
        return self.spent + cost <= self.usable

    def charge(self, cost: int) -> None:
        self.spent += cost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_KEY_RE = re.compile(r"([?&]key=)[^&\"'>\s]+")


def _redact(exc: Exception) -> str:
    return _KEY_RE.sub(r"\1REDACTED", str(exc))


def _is_quota_exceeded(exc: Exception) -> bool:
    message = str(exc)
    return "quotaExceeded" in message or "rateLimitExceeded" in message


def _date_to_rfc3339(d, end_of_day: bool = False) -> str:
    moment = dtime.max.replace(microsecond=0) if end_of_day else dtime.min
    return datetime.combine(d, moment, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def youtubeClient() -> Any:  # noqa: N802 - matches the course example name
    """Return a YouTube Data API v3 client using the configured key."""
    return build("youtube", "v3", developerKey=get_youtube_api_key())


# ---------------------------------------------------------------------------
# Phase 1 — search for candidate videos
# ---------------------------------------------------------------------------


def search_videos(
    client: Any,
    queries: tuple[str, ...],
    *,
    max_videos_per_query: int,
    published_after: str,
    published_before: str,
    quota: QuotaTracker,
) -> list[dict[str, Any]]:
    """One search.list call per query (50 results max). Dedup by video_id."""
    videos: list[dict[str, Any]] = []
    seen: set[str] = set()

    for query in queries:
        if not quota.can_afford(COST_SEARCH):
            print(f"  [budget] skipping search for {query!r} — would exceed quota")
            break
        try:
            quota.charge(COST_SEARCH)
            response = (
                client.search()
                .list(
                    q=query,
                    part="snippet",
                    type="video",
                    order="relevance",
                    maxResults=min(50, max_videos_per_query),
                    publishedAfter=published_after,
                    publishedBefore=published_before,
                )
                .execute()
            )
        except HttpError as exc:
            if _is_quota_exceeded(exc):
                print("  [budget] API-side quota exhausted; stopping search.")
                return videos
            print(f"  [warn] search failed for {query!r}: {_redact(exc)}")
            continue

        for item in response.get("items", []):
            vid = (item.get("id") or {}).get("videoId")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            snippet = item.get("snippet", {}) or {}
            videos.append({
                "video_id": vid,
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "channel_id": snippet.get("channelId", ""),
                "channel_title": snippet.get("channelTitle", ""),
                "published_at": snippet.get("publishedAt"),
                "query": query,
            })

    print(f"  → {len(videos)} unique videos discovered "
          f"(quota spent: {quota.spent}/{quota.usable})")
    return videos


# ---------------------------------------------------------------------------
# Phase 2 — attach statistics
# ---------------------------------------------------------------------------


def attach_statistics(client: Any, videos: list[dict[str, Any]],
                      *, quota: QuotaTracker) -> None:
    """Batch ``videos.list`` (50 ids per call) to fill view/like/comment counts."""
    by_id = {v["video_id"]: v for v in videos}
    ids = list(by_id)
    for start in range(0, len(ids), 50):
        if not quota.can_afford(COST_VIDEOS):
            print("  [budget] stats skipped — would exceed quota.")
            return
        chunk = ids[start:start + 50]
        quota.charge(COST_VIDEOS)
        try:
            response = client.videos().list(id=",".join(chunk), part="statistics").execute()
        except HttpError as exc:
            if _is_quota_exceeded(exc):
                print("  [budget] API-side quota exhausted; stats phase aborted.")
                return
            print(f"  [warn] stats failed: {_redact(exc)}")
            continue
        for item in response.get("items", []):
            stats = item.get("statistics", {}) or {}
            v = by_id.get(item.get("id"))
            if v is None:
                continue
            v["view_count"] = int(stats["viewCount"]) if stats.get("viewCount") else None
            v["like_count"] = int(stats["likeCount"]) if stats.get("likeCount") else None
            v["comment_count"] = int(stats["commentCount"]) if stats.get("commentCount") else None


# ---------------------------------------------------------------------------
# Phase 3 — comments for each video
# ---------------------------------------------------------------------------


def _iso_to_epoch(value: str | None) -> int | None:
    """Convert an ISO 8601 UTC string to integer epoch seconds (or None)."""
    if not value:
        return None
    try:
        # YouTube emits e.g. "2026-05-04T00:00:00Z" — normalise the trailing Z.
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return None


def _normalise_comment(item: dict[str, Any], *, parent_id: str | None = None,
                       is_reply: bool = False) -> dict[str, Any]:
    snippet = item.get("snippet", {}) or {}
    if "topLevelComment" in snippet:
        top = snippet.get("topLevelComment", {}) or {}
        s = top.get("snippet", {}) or {}
        cid = top.get("id")
        reply_count = int(snippet.get("totalReplyCount") or 0)
    else:
        s = snippet
        cid = item.get("id")
        reply_count = 0
    published_at = s.get("publishedAt")
    return {
        "comment_id": cid,
        "parent_id": parent_id,
        "author": s.get("authorDisplayName"),
        "text": s.get("textDisplay", ""),
        "published_at": published_at,
        # Integer epoch seconds in addition to the ISO string so cross-platform
        # daily aggregation + lag correlation against FRED can join on the same
        # unit Reddit uses (created_utc).
        "published_utc": _iso_to_epoch(published_at),
        "updated_at": s.get("updatedAt"),
        "like_count": int(s.get("likeCount") or 0),
        "reply_count": reply_count,
        "is_reply": is_reply,
    }


def _fetch_extra_replies(
    client: Any,
    parent_id: str,
    *,
    remaining: int,
    quota: QuotaTracker,
) -> tuple[list[dict[str, Any]], str | None]:
    """Paginate ``comments.list?parentId=...`` for the missing tail of a thread."""
    replies: list[dict[str, Any]] = []
    page_token: str | None = None
    try:
        while remaining > 0:
            if not quota.can_afford(COST_COMMENTS):
                return replies, "budget_exhausted"
            quota.charge(COST_COMMENTS)
            response = (
                client.comments()
                .list(
                    parentId=parent_id,
                    part="snippet",
                    maxResults=min(100, remaining),
                    pageToken=page_token,
                    textFormat="plainText",
                )
                .execute()
            )
            for item in response.get("items", []):
                replies.append(_normalise_comment(item, parent_id=parent_id, is_reply=True))
                remaining -= 1
                if remaining <= 0:
                    break
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    except HttpError as exc:
        if _is_quota_exceeded(exc):
            return replies, "budget_exhausted"
        return replies, _redact(exc)
    return replies, None


def fetch_comments_for_video(
    client: Any,
    video_id: str,
    *,
    max_comments: int,
    quota: QuotaTracker,
) -> tuple[list[dict[str, Any]], str | None]:
    """Page ``commentThreads.list`` for one video, with full reply pagination.

    For each top-level thread whose ``totalReplyCount`` exceeds the
    number of replies embedded in ``commentThreads.list``, we follow up
    with ``comments.list?parentId=...`` to recover the tail.  ``order``
    is set to ``time`` so cascade-timing analyses (nb 06) see comments
    in chronological order rather than by YouTube's relevance ranking.
    """
    comments: list[dict[str, Any]] = []
    page_token: str | None = None
    error: str | None = None
    try:
        while len(comments) < max_comments:
            if not quota.can_afford(COST_COMMENT_THREADS):
                return comments, "budget_exhausted"
            quota.charge(COST_COMMENT_THREADS)
            response = (
                client.commentThreads()
                .list(
                    videoId=video_id,
                    part="snippet,replies",
                    maxResults=min(100, max_comments - len(comments)),
                    pageToken=page_token,
                    textFormat="plainText",
                    order="time",
                )
                .execute()
            )
            for item in response.get("items", []):
                top = _normalise_comment(item)
                comments.append(top)
                if len(comments) >= max_comments:
                    break

                embedded = (item.get("replies", {}) or {}).get("comments", []) or []
                embedded_ids: set[str] = set()
                for reply in embedded:
                    rid = reply.get("id")
                    if rid:
                        embedded_ids.add(rid)
                    comments.append(_normalise_comment(reply, parent_id=top["comment_id"], is_reply=True))
                    if len(comments) >= max_comments:
                        break

                # If the thread has more replies than commentThreads.list
                # embedded, paginate comments.list to recover them.
                if (
                    len(comments) < max_comments
                    and top["reply_count"]
                    and top["reply_count"] > len(embedded)
                ):
                    extras, sub_err = _fetch_extra_replies(
                        client,
                        top["comment_id"],
                        remaining=max_comments - len(comments),
                        quota=quota,
                    )
                    for reply in extras:
                        if reply["comment_id"] in embedded_ids:
                            continue
                        comments.append(reply)
                        if len(comments) >= max_comments:
                            break
                    if sub_err == "budget_exhausted":
                        return comments, "budget_exhausted"
                    if sub_err and not error:
                        error = sub_err
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    except HttpError as exc:
        if _is_quota_exceeded(exc):
            return comments, "budget_exhausted"
        return comments, _redact(exc)
    return comments, error


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def fetch_youtube_data(
    *,
    queries: tuple[str, ...] = YOUTUBE_QUERIES,
    max_videos_per_query: int = YOUTUBE_MAX_VIDEOS_PER_QUERY,
    max_comments_per_video: int = YOUTUBE_MAX_COMMENTS_PER_VIDEO,
    daily_quota: int = DEFAULT_DAILY_QUOTA,
    safety_buffer: int = DEFAULT_SAFETY_BUFFER,
    out_path: Path,
) -> dict[str, Any]:
    """Run search → stats → comments. Writes a single JSON file."""
    quota = QuotaTracker(daily_limit=daily_quota, safety_buffer=safety_buffer)
    client = youtubeClient()

    published_after = _date_to_rfc3339(PROJECT_START_DATE)
    published_before = _date_to_rfc3339(PROJECT_END_DATE, end_of_day=True)

    print(f"YouTube collection — daily quota: {quota.usable}/{quota.daily_limit} usable\n")
    print(f"Phase 1/3: searching {len(queries)} queries")
    videos = search_videos(
        client, queries,
        max_videos_per_query=max_videos_per_query,
        published_after=published_after,
        published_before=published_before,
        quota=quota,
    )

    print(f"\nPhase 2/3: video statistics ({len(videos)} videos)")
    attach_statistics(client, videos, quota=quota)

    print(f"\nPhase 3/3: comments (cap {max_comments_per_video}/video)")
    n_with_error = n_zero = n_partial = 0
    for index, video in enumerate(videos, 1):
        if not quota.can_afford(COST_COMMENT_THREADS):
            video["comment_error"] = "budget_exhausted"
            video["partial_due_to_budget"] = True
            video["comments"] = []
            n_partial += 1
            continue
        comments, error = fetch_comments_for_video(
            client, video["video_id"],
            max_comments=max_comments_per_video,
            quota=quota,
        )
        if error == "budget_exhausted":
            video["partial_due_to_budget"] = True
            n_partial += 1
        elif error:
            video["comment_error"] = error
            n_with_error += 1
        video["comments"] = comments
        if not comments:
            n_zero += 1
        print(
            f"  [{index}/{len(videos)}] {video['video_id']} "
            f"({len(comments)} comments, quota {quota.spent}/{quota.usable})"
        )

    total = sum(len(v["comments"]) for v in videos)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "collected_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "YouTube Data API v3",
        "parameters": {
            "queries": list(queries),
            "max_videos_per_query": max_videos_per_query,
            "max_comments_per_video": max_comments_per_video,
            "published_after": published_after,
            "published_before": published_before,
            "daily_quota": daily_quota,
            "safety_buffer": safety_buffer,
        },
        "counts": {
            "videos": len(videos),
            "comments_total": total,
            "videos_with_zero_comments": n_zero,
            "videos_with_comment_error": n_with_error,
            "videos_partial_due_to_budget": n_partial,
            "quota_units_spent": quota.spent,
            "quota_units_remaining": quota.remaining,
        },
        "videos": videos,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nWrote {out_path}")
    print(
        f"Done: {len(videos)} videos, {total:,} comments, "
        f"quota spent {quota.spent}/{quota.usable}"
    )
    return payload


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-videos-per-query", type=int, default=YOUTUBE_MAX_VIDEOS_PER_QUERY)
    parser.add_argument("--max-comments-per-video", type=int, default=YOUTUBE_MAX_COMMENTS_PER_VIDEO)
    parser.add_argument("--daily-quota", type=int, default=DEFAULT_DAILY_QUOTA)
    parser.add_argument("--safety-buffer", type=int, default=DEFAULT_SAFETY_BUFFER)
    parser.add_argument(
        "--out",
        type=Path,
        default=RAW_YOUTUBE_DIR / f"youtube_hormuz_jetfuel_{_utc_stamp()}.json",
    )
    args = parser.parse_args(argv)
    fetch_youtube_data(
        max_videos_per_query=args.max_videos_per_query,
        max_comments_per_video=args.max_comments_per_video,
        daily_quota=args.daily_quota,
        safety_buffer=args.safety_buffer,
        out_path=args.out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
