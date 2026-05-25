# Methodology — Topic modelling

## Course grounding and choice of model

Topic modelling follows the Week 6 course content and uses **Latent Dirichlet
Allocation (LDA)** (Blei, Ng & Jordan, 2003) as the headline method. LDA is a
generative probabilistic model in which each document is a mixture over latent
topics and each topic is a distribution over the vocabulary; inference recovers
both the per-document topic mixtures and the per-topic word distributions from
the observed word counts. LDA is fitted on raw word-count vectors rather than
TF-IDF because its generative assumptions are over counts. **Non-negative
Matrix Factorisation (NMF)** is also computed as an optional robustness
comparison, but does not drive the final topic labels, prevalence tables, or
any downstream notebook (NB6 diffusion, NB7 community profiling). The choice
of LDA as headline and NMF as comparison is recorded in the §8 decision log of
NB5.

## Inputs and preprocessing

The topic model takes as input the lemmatised, lower-cased comment text
produced in NB1 (YouTube) and NB2 (Reddit). Preprocessing steps already
applied upstream include URL stripping, mention removal, contraction
expansion, NLTK-WordNet lemmatisation, and a domain-stopword pass that drops
high-frequency operational vocabulary (e.g. "comment", "post", "subreddit",
platform names) so that those terms do not dominate the topic space. A small
*topic-specific* additional stopword list is applied at vectorisation time
inside NB5 to remove generic conversational fillers ("would", "think", "like",
"people", "really", and so on) that are uninformative for topical content but
not generic enough to remove during general preprocessing.

## Vocabulary construction

Each platform's lemmatised corpus is vectorised with a `CountVectorizer`
configured with `ngram_range=(1, 2)`, `max_features=5000`, `max_df=0.95`, and a
**platform-adaptive** `min_df`. The adaptive rule reduces `min_df` to 2 for
small corpora and raises it to 5 for corpora exceeding one thousand documents,
so that very rare terms do not become spurious topic centres on the smaller
platform while still being filtered out on the larger one. The token pattern
keeps only alphabetic tokens of length ≥3, which removes pure numerics and
short noise tokens that the upstream preprocessor lets through.

## Why platforms are modelled separately

Reddit and YouTube are modelled with **two independent LDA fits**, not one
joint fit on a stacked corpus. Three reasons motivate this choice:

1. **Register and length differ.** Reddit comments are long-form, debate-heavy,
   and rich in subreddit jargon; YouTube comments are short, reactive, and
   broadcaster-bounded. A joint LDA must find topics that explain both
   registers simultaneously, which empirically yields blurrier, lower-coherence
   themes than fitting each register on its own.
2. **Corpus sizes and vocabularies differ.** The platforms have different
   document counts and different lexical fingerprints; a shared vocabulary
   and shared `min_df` would force compromises on both sides. The adaptive
   `min_df` above is the per-platform expression of this concern.
3. **Cross-platform comparison is a research question, not a fixed result.**
   Topic overlap between Reddit and YouTube is itself one of the analyses
   we want to perform (§11 of NB5). That comparison is only meaningful if
   the two topic spaces are fitted independently — otherwise the platforms
   share a topic space by construction and the question reduces to
   *"do the two platforms place mass on the same shared topics?"*, which
   omits the structural question of *whether the platforms find the same
   themes at all*.

A practical consequence is that the two LDA models produce two independent
`topic_id` index spaces. Reddit topic 0 and YouTube topic 0 are unrelated
objects — they are slot 0 of two unrelated models — and any human-readable
labelling must therefore be platform-specific. Comparison between platforms is
done after fitting via term-set overlap (Jaccard) on the top-terms-per-topic.

## Selecting the number of topics k

The number of topics `k` is selected per platform from a sweep over
`k ∈ {3, 4, 5, 6, 7, 8}` (truncated for very small corpora). For each
candidate `k` the LDA model is fitted on the count matrix and **perplexity** on
the training corpus is recorded; the chosen `k` is the one minimising
perplexity. The same `k` values are also evaluated under NMF using **UMass
coherence** (Mimno et al., 2011) as a secondary, model-agnostic
interpretability diagnostic. Both diagnostics are plotted side by side in
NB5 §8.

The final `k` per platform was deliberately kept small (three topics each)
for two reasons: (i) the assignment goal is interpretable social-media
analysis rather than fine-grained ontological extraction, and (ii) at this
corpus size larger `k` values produce fragile micro-topics whose top terms
overlap heavily and whose representative comments are difficult to
distinguish on inspection.

## Manual topic labelling

Topic numbers are not informative on their own. After LDA is fitted at the
selected `k`, each topic is assigned a human-readable title manually by
inspecting (a) the top fifteen terms by topic-word probability and (b) the
five highest-probability representative comments under that topic. The two
title dictionaries are recorded in code in NB5 §10
(`reddit_lda_topic_labels`, `youtube_lda_topic_labels`) and are the *only*
place where human judgement enters the topic pipeline. All downstream tables
and plots — topic prevalence by subreddit, by channel, by time, by
sentiment, by cascade — read these labels through a single lookup function
so a relabelling can be applied in one place and propagated everywhere.

For this corpus, Reddit's three topics resolved as
*Iran–US geopolitical conflict and war risk*,
*Strait of Hormuz and oil-market disruption*, and
*Trump, markets, and US economic reaction*.
YouTube's three topics resolved as
*Global power politics and US–China rivalry*,
*Iran–US conflict, Israel, and military escalation*, and
*Oil, gas prices, and global energy supply*.
The Iran–US conflict frame appears on both platforms; the Reddit corpus has
a distinct Trump/markets/economic-reaction frame that YouTube does not
mirror, and YouTube has a distinct US–China-rivalry frame that Reddit does
not mirror. This is consistent with Reddit's more domestically-political
register and YouTube's broadcaster-bounded geopolitical framing.

## Stability and robustness checks

Three checks are run to guard against over-claiming the topic structure.
First, the LDA fit is **repeated at multiple random seeds** at the chosen `k`
and the per-seed top-term sets are compared (NB5 §16); a topic whose top
terms are stable across seeds is treated as a real corpus structure, and a
topic whose top terms reshuffle across seeds is flagged as fragile in the
report. Second, the LDA topic-probability distribution per comment is
reported so that the share of comments whose dominant-topic posterior is
weak (e.g. < 0.5) is visible — a high share of weakly-dominant comments would
mean the model is averaging across topics rather than discriminating.
Third, the optional NMF run at the same `k` provides a model-agnostic
counterfactual: if NMF and LDA produce broadly the same top-term sets, the
themes are stable across model families; if they diverge sharply, the
themes should be treated as model-dependent.

## Downstream use

The dominant-topic assignment per comment is exported to
`data/processed/03_sentiment_topics/topics_lda_platform_dominant_per_comment_labelled.csv`
and is consumed by subsequent notebooks. NB6 uses it to compute per-cascade
dominant topics, topic-entropy, and topic-by-diffusion contingency tables;
NB7 uses it as one of the axes when profiling Louvain communities; the
report's results chapters quote the per-platform topic rankings and the
cross-platform Jaccard overlap from these tables.

## What this analysis can and cannot conclude

The topic model describes *which themes the platform's comments cluster
around*, weighted by how frequently each theme is discussed. It cannot
identify the *driving topic* of any individual conversation when that
conversation crosses themes — LDA assigns a mixture rather than a hard
label. We hedge this in the report by reporting both the dominant-topic
share and the per-cascade topic entropy: low entropy means the dominant
label is informative for the whole cascade; high entropy means the cascade
sprawls across themes and the dominant label captures only its plurality
theme.

## References

- Blei, D. M., Ng, A. Y., & Jordan, M. I. (2003). Latent Dirichlet
  Allocation. *Journal of Machine Learning Research*, 3, 993–1022.
- Mimno, D., Wallach, H. M., Talley, E., Leenders, M., & McCallum, A.
  (2011). Optimizing semantic coherence in topic models. *Proceedings of
  EMNLP*, 262–272.
- Pedregosa, F., et al. (2011). scikit-learn: Machine Learning in Python.
  *Journal of Machine Learning Research*, 12, 2825–2830.
