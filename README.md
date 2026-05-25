# Framing the Strait of Hormuz Crisis on Social Media

**COSC2671 Social Media and Network Analysis - Assignment 2 (PG Group 3)**
**Team:** Matteo Omizzolo (`s4230379`) and Emilija Milanovic (`s4233176`)

## Project Aim

This project examines how Reddit and YouTube communities framed the 2026 Strait
of Hormuz crisis and how sentiment, topics, interaction structure, community
patterns, influence, and diffusion differ across platforms.

## Data Sources

- Reddit public thread JSON for topic-focused discussions across eight subreddits.
- YouTube Data API v3 comments and replies for six topic-focused queries.
- FRED daily energy-price series used as external temporal context only.

The report describes the full collection and processed analysis dataset. The
repository intentionally contains only a compact, anonymised representative
sample; full raw and processed social-media datasets are omitted for size and
privacy-conscious submission.

## Submission Files

| File or directory | Purpose |
|---|---|
| `Report_s4230379_s4233176_PG_Group_3.pdf` | Final group report supplied for submission. |
| `Worksheet_s4230379_s4233176_PG_Group_3.pdf` | Worksheet template for completion with actual planning, hours, and reflection details. |
| `Access_s4230379_s4233176_PG_Group_3.txt` | Repository-access demonstration record with fields to finalise after GitHub setup. |
| `data/sample/` | Representative data sample covering NLP/text fields and network edges. |
| `notebooks/` | Ordered analysis workflow. Stored without executed outputs to remove local path disclosures and transient displayed data. |
| `src/` | Reusable data-collection and configuration scripts. |
| `lexicons/` | Lexicons used by the analysis workflow. |
| `docs/topic_modelling_methodology.md` | Supporting method note. |
| `requirements.txt` | Python dependencies. |

## Recommended Run Order

Data collection is optional for assessment review because it needs fresh API
access and recreates data omitted from this submission. To reproduce from a
permitted local full-data copy, run:

```text
Optional collection:
  python src/reddit_collect.py
  python src/youtube_collect.py       # requires the user's own YouTube API key
  python src/price_collect.py

Analysis notebooks:
  notebooks/01_youtube_preprocessing.ipynb
  notebooks/02_reddit_preprocessing.ipynb
  notebooks/03_basic_nlp.ipynb
  notebooks/04_network_analysis.ipynb
  notebooks/05_topics_sentiment.ipynb
  notebooks/06_diffusion_analysis.ipynb
  notebooks/07_community_sentiment_topics.ipynb
  notebooks/08_platform_comparison.ipynb
```

The small sample demonstrates data structure and analysis fields; it is not a
drop-in replacement for all full-data notebook inputs.

## Environment Setup

Use Python 3.10 or later:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
jupyter lab
```

Notebook 05 lists `torch` and `transformers` as optional heavy dependencies for
the transformer sentiment stage.

## Expected Inputs And Outputs

| Stage | Expected input | Principal output |
|---|---|---|
| Collection | Public API/web endpoints and locally supplied credentials where required | Raw JSON under `data/raw/` (excluded) |
| Preprocessing | Raw Reddit/YouTube JSON | Clean comment tables and metadata under `data/processed/` (excluded) |
| NLP/topic/sentiment | Processed comment tables and lexicons | Topic and sentiment summaries (excluded; results reported in PDF) |
| Network/diffusion/comparison | Processed comments and upstream results | Edge/community/diffusion tables and figures (excluded; results reported in PDF) |

## Representative Data Sample

`data/sample/` totals approximately 328 KB, safely below the 10 MB limit. It
contains anonymised comment-level NLP/community fields plus directed Reddit and
YouTube reply-edge samples. Author identifiers are hashed; raw author usernames
are not included. Public platform/content identifiers retained in the sample are
provided only to demonstrate schema and network linkage.

## Privacy And Access

This clean repository does not include `.env` files, API keys, access tokens,
passwords, private credentials, raw data dumps, full processed datasets, local
virtual environments, cached bytecode, old Git history, or local execution-path
outputs. A user who elects to collect new YouTube data must supply their own API
key through their local environment and must not commit it.
