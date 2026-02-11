import json
import re
from pathlib import Path
from typing import Iterable
import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel


QUESTION_START_RE = re.compile(
    r"(?:^|\n)\s*(\d+)\s*(\([a-z]\))?\s*(\([ivx]+\))?",
    re.IGNORECASE,
)

QUERY_NOISE_TERMS = {"purpose", "function", "role"}
ACRONYM_EXPANSIONS = {
    "alu": "arithmetic logic unit",
    "cu": "control unit",
    "ram": "random access memory",
    "rom": "read only memory",
    "cpu": "central processing unit",
}


def _iter_text_files(text_dir: Path, ms_only: bool) -> Iterable[Path]:
    for path in sorted(text_dir.glob("*.txt")):
        if ms_only and "_ms_" not in path.name.lower():
            continue
        yield path


def _split_into_chunks(text: str) -> list[dict]:
    # Split by question id patterns anywhere in text to avoid line-start dependency.
    matches = list(QUESTION_START_RE.finditer(text))
    if not matches:
        return [{"text": text.strip(), "qid": None}]

    chunks: list[dict] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk_text = text[start:end].strip()
        if not chunk_text:
            continue
        qid = _format_qid(m)
        chunks.append({"text": chunk_text, "qid": qid})
    return chunks


def _format_qid(match: re.Match) -> str:
    parts = [match.group(1)]
    if match.group(2):
        parts.append(match.group(2))
    if match.group(3):
        parts.append(match.group(3))
    return " ".join(parts).strip()


def build_index(text_dir: Path, index_dir: Path, ms_only: bool = True) -> tuple[Path, Path]:
    index_dir.mkdir(parents=True, exist_ok=True)

    chunks = []
    for txt_path in _iter_text_files(text_dir, ms_only):
        text = txt_path.read_text(encoding="utf-8", errors="ignore")
        for chunk in _split_into_chunks(text):
            chunks.append(
                {
                    "text": chunk["text"],
                    "source": txt_path.name,
                    "qid": chunk["qid"],
                }
            )

    vectorizer, matrix = build_vector_index(chunks)

    data_path = index_dir / "chunks.json"
    data_path.write_text(json.dumps(chunks, indent=2), encoding="utf-8")

    model_path = index_dir / "index.pkl"
    joblib.dump({"vectorizer": vectorizer, "matrix": matrix}, model_path)

    return data_path, model_path


def build_vector_index(chunks: list[dict]) -> tuple[TfidfVectorizer, object]:
    if not chunks:
        raise RuntimeError("No chunks available to build index.")
    corpus = [_normalize_text(c["text"]) for c in chunks]
    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        preprocessor=_normalize_text,
    )
    matrix = vectorizer.fit_transform(corpus)
    return vectorizer, matrix


def load_index(index_dir: Path) -> tuple[list[dict], TfidfVectorizer, object]:
    data_path = index_dir / "chunks.json"
    model_path = index_dir / "index.pkl"
    chunks = json.loads(data_path.read_text(encoding="utf-8"))
    model = joblib.load(model_path)
    return chunks, model["vectorizer"], model["matrix"]


def query_index(
    question_text: str,
    chunks: list[dict],
    vectorizer: TfidfVectorizer,
    matrix,
    top_k: int = 3,
    question_id: str | None = None,
) -> list[dict]:
    filtered_idx = list(range(len(chunks)))
    if question_id:
        qid_norm = question_id.strip().lower()
        filtered_idx = [
            i for i in filtered_idx if (chunks[i].get("qid") or "").lower() == qid_norm
        ]

    if not filtered_idx:
        return []

    query_terms = _extract_query_terms(question_text)
    expanded_query = _expand_query(question_text, query_terms)
    query_vec = vectorizer.transform([expanded_query])
    scores = linear_kernel(query_vec, matrix).flatten()
    scored = []
    for i in filtered_idx:
        boost = _keyword_boost(query_terms, chunks[i]["text"])
        scored.append((i, float(scores[i]) + boost))
    scored.sort(key=lambda x: x[1], reverse=True)
    results = [chunks[i] for i, s in scored[:top_k] if s > 0]
    return results
def _normalize_text(text: str) -> str:
    # Normalize common hyphenation variants that affect retrieval.
    text = re.sub(r"\breal\s*-\s*time\b", "real-time", text, flags=re.IGNORECASE)
    text = re.sub(r"\breal\s+time\b", "real-time", text, flags=re.IGNORECASE)
    return text


def _extract_query_terms(question_text: str) -> set[str]:
    terms = set()
    for token in re.findall(r"[A-Za-z0-9\-]+", question_text.lower()):
        if len(token) < 2:
            continue
        if token in QUERY_NOISE_TERMS:
            continue
        terms.add(token)
    return terms


def _expand_query(question_text: str, terms: set[str]) -> str:
    expansions = [ACRONYM_EXPANSIONS[t] for t in terms if t in ACRONYM_EXPANSIONS]
    if not expansions:
        return question_text
    return f"{question_text} {' '.join(expansions)}"


def _keyword_boost(query_terms: set[str], chunk_text: str) -> float:
    if not query_terms:
        return 0.0
    chunk_lower = chunk_text.lower()
    hits = 0
    for t in query_terms:
        if t in ACRONYM_EXPANSIONS:
            if t in chunk_lower or ACRONYM_EXPANSIONS[t] in chunk_lower:
                hits += 1
            continue
        if t in chunk_lower:
            hits += 1
    return 0.2 * (hits / max(1, len(query_terms)))
