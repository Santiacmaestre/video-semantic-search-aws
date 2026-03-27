# Search Ranking Tuning — Named Entity & Title Matching

## Problem

Queries containing named entities or video titles (e.g., "Picture of 3 men hanging on the wall in meridian", "kevin taking a phone call next to a vintage car") were not ranking the correct video segments at the top. The correct segments appeared in results but were buried below irrelevant matches.

Two root causes:

1. **LLM weight analyzer gave too little weight to metadata.** Haiku consistently assigned metadata=0.3 even when queries contained proper nouns, because abstract rules like "set metadata >= 0.5" were ignored in favor of the model's own judgment that visual descriptions were more important.

2. **BM25 field configuration diluted name/title matches.** The `people` field was `keyword` type (exact match only), so partial names like "kevin" couldn't match "Kevin Kilner". Field boosts were too low for `title` and `people` relative to `caption`, so common words in captions outscored rare name/title matches.

## Changes Made

### 1. Prompt optimization — few-shot patterns over abstract rules

**File:** `lib/prompt_analyzer.py`

Replaced verbose abstract rules with concise weight patterns using bracket placeholders. The model follows concrete examples more reliably than instructions it can rationalize around.

Key patterns:
```
- Pure visual, no names: "red car driving fast" → visual=1.0
- Visual + a name/title: "car chase in [name]" → metadata=0.5, visual=0.4, transcription=0.1
- Person doing something: "[Person] scores a goal" → metadata=0.3, visual=0.6, transcription=0.1
- Speech content: "talking about climate change" → transcription=0.5, visual=0.2, audio=0.1, metadata=0.2
- Sound-focused: "loud explosion sound" → audio=0.7, visual=0.3
```

The patterns are generic (no hardcoded queries) and cover the main query archetypes. The model now consistently assigns metadata=0.4–0.5 for queries with names/titles.

Full prompt:

```
Analyze video search queries and assign weights (0.0-1.0) for four modalities.
Weights must sum to 1.0.

Return ONLY valid JSON in this exact format:
{
  "visual": 0.0,
  "audio": 0.0,
  "transcription": 0.0,
  "metadata": 0.0,
  "reasoning": "brief explanation"
}

Modality definitions:
- visual: Appearance, colors, objects, actions, scenes, physical descriptions
- audio: Non-speech sounds, music, ambient noise, sound effects
- transcription: Spoken words, dialogue, narration, speech content
- metadata: Person names, video titles, genre, text on screen, captions, named entities, factual attributes

Weight assignment rules:
1. Focus on semantic intent, not surface-level word choice. Synonymous phrases describing the same action or concept must receive identical weights regardless of the specific words used.
2. When a query contains ANY proper noun, named entity, or distinctive/uncommon term, metadata should be 0.4. BM25 keyword matching is the only way to match names and titles. Remaining weight goes to the dominant content modality (usually visual).
3. Only assign audio weight when the query explicitly describes non-speech sounds, music, or acoustic qualities.
4. Assign transcription weight only when the query is about spoken words or dialogue content.

Weight patterns (follow these strictly):
- Pure visual, no names: "red car driving fast" → visual=1.0
- Visual + a name/title: "car chase in [name]" → metadata=0.5, visual=0.4, transcription=0.1
- Person doing something: "[Person] scores a goal" → metadata=0.3, visual=0.6, transcription=0.1
- Speech content: "talking about climate change" → transcription=0.5, visual=0.2, audio=0.1, metadata=0.2
- Sound-focused: "loud explosion sound" → audio=0.7, visual=0.3
```

### 2. BM25 field boost rebalancing

Changed the `multi_match` field boosts:

| Field | Before | After | Rationale |
|-------|--------|-------|-----------|
| `title` | `^3` | `^5` | Title matches should strongly dominate when a query contains a video name |
| `people` | `^3` | `^5` | Name matches should strongly dominate when a query contains a person name |
| `caption` | `^2` | `^1` (no boost) | Captions contain common words ("men", "talking", "car") that match too broadly; removing the boost prevents them from drowning out specific name/title matches |

### 3. `people` field type change — keyword → text

**File:** `lib/opensearch_client.py`

Changed the OpenSearch mapping for `people` from `keyword` to `text`:

```python
# Before
"people": {"type": "keyword"}

# After
"people": {"type": "text"}
```

- `keyword`: Stores "Kevin Kilner" as a single token. Only exact match works. Searching "kevin" returns nothing.
- `text`: Tokenizes "Kevin Kilner" into ["kevin", "kilner"]. Partial name searches like "kevin" now match.

**Note:** This is a mapping change — requires reindexing (delete project and recreate). Existing indices are not affected retroactively.

### 4. Lambda layer alignment

Updated all Lambda functions that interact with OpenSearch to the same shared layer version:

- `video-search-v2-search-function` — queries the index
- `video-search-v2-project-function` — creates/deletes the index
- `video-search-v2-merge` — indexes documents

Previously only the search function was updated during iteration, while the project function (which creates the index mapping) was on an old layer version. This caused new projects to still get the old `keyword` mapping.

## Results

**Query: "Picture of 3 men hanging on the wall in meridian"**
- Weights: visual=0.5, metadata=0.5
- #1: Netflix Open Content Meridian — "three men whose photos are on a bulletin board" (score 0.705)

**Query: "kevin taking a phone call next to a vintage car"**
- Weights: visual=0.6, metadata=0.4
- #1: Netflix Open Content Meridian — Kevin Kilner "talking on a phone while standing by a car" (score 0.998)

## Lessons Learned

1. **Few-shot patterns > abstract rules** for steering small LLMs. Haiku ignored "set metadata >= 0.5" but followed concrete `[Person] does X → metadata=0.3, visual=0.6` patterns consistently.
2. **Field boosts matter more than weight tuning** when BM25 queries contain a mix of rare terms (names) and common terms (actions/objects). Boosting `people^5` and `title^5` while removing the caption boost was the biggest single improvement.
3. **Keep all Lambda functions on the same layer version** when iterating on shared library code. The index mapping is set at project creation time by the project function, not at search time.
