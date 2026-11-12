"""Closed-vocabulary keyword search over the evidence corpus (docs/23). SQLite FTS5 with BM25 ranking, in memory, offline:
no embedding model, no vector store, no network.

Only single words from a closed vocabulary (the words of the rules' own wording and the passages' locations) can be
searched; anything else, and above all free text such as a patient's words, is refused. Meant for the assistant's
read-only evidence tool (Phase 5); the case screen looks passages up by rule id, not by search.
"""

from __future__ import annotations

import re
import sqlite3

from app.evidence.corpus import Corpus, rule_catalogue
from app.rules.sources import SOURCES

_WORD = re.compile(r"[a-z]{3,40}")


class Index:
    def __init__(self, corpus: Corpus):
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.execute("CREATE VIRTUAL TABLE passages USING fts5(passage_id UNINDEXED, body)")
        catalogue = rule_catalogue()
        vocab: set[str] = set()
        for p in corpus.passages:
            reasons = " ".join(catalogue[r][1] for r in p.rule_ids if r in catalogue)
            body = " ".join(x for x in (p.locator, SOURCES[p.source_id].citation, reasons, p.text or "") if x)
            self._db.execute("INSERT INTO passages (passage_id, body) VALUES (?, ?)", (p.passage_id, body))
            vocab.update(_WORD.findall((p.locator + " " + reasons).lower()))
        self.vocabulary = frozenset(vocab)

    def search(self, term: str, limit: int = 10) -> list[str]:
        """Passage ids ranked by BM25 for one vocabulary word. Raises ValueError for anything else."""
        word = term.strip().lower()
        if not _WORD.fullmatch(word) or word not in self.vocabulary:
            raise ValueError("search takes one word from the closed vocabulary")
        rows = self._db.execute("SELECT passage_id FROM passages WHERE passages MATCH ? ORDER BY bm25(passages) LIMIT ?",
                                (f'"{word}"', limit)).fetchall()
        return [r[0] for r in rows]


def build(corpus: Corpus) -> Index:
    return Index(corpus)
