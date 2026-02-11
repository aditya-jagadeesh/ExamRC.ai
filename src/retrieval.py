import re
from typing import Iterable

STOPWORDS = {
    "the", "and", "or", "to", "of", "a", "an", "in", "on", "for", "with", "by", "is", "are", "was",
    "were", "be", "been", "being", "that", "this", "these", "those", "as", "at", "from", "it", "its",
    "into", "over", "under", "between", "within", "without", "use", "used", "using", "can", "may",
    "will", "would", "should", "could", "do", "does", "did", "done", "what", "which", "how", "why",
    "explain", "describe", "identify", "state", "give", "define", "outline", "compare", "contrast",
}


def _normalize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in STOPWORDS]
    return tokens


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _score_chunk(query_tokens: Iterable[str], chunk_tokens: Iterable[str]) -> float:
    qset = set(query_tokens)
    cset = set(chunk_tokens)
    if not qset or not cset:
        return 0.0
    return len(qset & cset) / len(qset | cset)


def find_best_chunks(question_text: str, ms_text: str, max_chunks: int = 3) -> list[str]:
    query_tokens = _normalize(question_text)
    chunks = _chunk_text(ms_text)
    scored = []
    for chunk in chunks:
        score = _score_chunk(query_tokens, _normalize(chunk))
        scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for s, c in scored[:max_chunks] if s > 0]
