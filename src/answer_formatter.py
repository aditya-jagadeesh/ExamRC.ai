import re
from collections import Counter
from typing import Iterable

STOPWORDS = {
    "the", "and", "or", "to", "of", "a", "an", "in", "on", "for", "with", "by", "is", "are", "was",
    "were", "be", "been", "being", "that", "this", "these", "those", "as", "at", "from", "it", "its",
    "into", "over", "under", "between", "within", "without", "use", "used", "using", "can", "may",
    "will", "would", "should", "could", "do", "does", "did", "done", "what", "which", "how", "why",
}


def _normalize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [t for t in text.split() if t and t not in STOPWORDS]
    return tokens


def _top_keywords(text: str, max_terms: int) -> list[str]:
    tokens = _normalize(text)
    counts = Counter(tokens)
    return [t for t, _ in counts.most_common(max_terms)]


def _depth_from_command_word(command_word: str, marks: int | None) -> int:
    # Heuristic: use marks if present; otherwise map by command word
    if marks is not None:
        return max(2, min(6, marks))

    shallow = {"identify", "state", "give", "define"}
    medium = {"describe", "outline", "compare", "contrast"}
    deep = {"explain", "discuss", "evaluate", "justify"}

    if command_word in shallow:
        return 2
    if command_word in medium:
        return 3
    if command_word in deep:
        return 4
    return 3


def format_answer(
    question_text: str,
    command_word: str,
    marks: int | None,
    ms_chunks: Iterable[str],
) -> tuple[str, str]:
    combined = "\n\n".join(ms_chunks).strip()
    if not combined:
        # Fallback when retrieval fails
        exact = "- Insufficient mark-scheme match found. Please refine the question text."
        short = "I could not find a close match in the mark scheme."
        return exact, short

    depth = _depth_from_command_word(command_word, marks)
    keywords = _top_keywords(combined, max_terms=depth + 2)

    exact_lines = [f"- {kw}" for kw in keywords[:depth]]
    exact_answer = "\n".join(exact_lines)

    # Short explanation from the top chunk, trimmed
    summary = combined.replace("\n", " ")
    summary = re.sub(r"\s+", " ", summary).strip()
    short_explanation = summary[:240] + ("..." if len(summary) > 240 else "")

    return exact_answer, short_explanation
