from typing import Iterable

from indexing import _split_into_chunks, build_vector_index
from supabase_store import SupabaseConfig, SupabaseStore


def _chunked(values: list[int], size: int = 100) -> Iterable[list[int]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _fetch_all_paper_text_rows(store: SupabaseStore, page_size: int = 500) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        resp = store.session.get(
            store._rest_url("paper_texts"),
            params={
                "select": "id,paper_id,text_content,source_url,metadata",
                "order": "id.asc",
                "limit": str(page_size),
                "offset": str(offset),
            },
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
    return rows


def _fetch_papers_by_ids(store: SupabaseStore, paper_ids: list[int]) -> dict[int, dict]:
    papers: dict[int, dict] = {}
    for batch in _chunked(paper_ids, size=100):
        ids_csv = ",".join(str(x) for x in batch)
        resp = store.session.get(
            store._rest_url("papers"),
            params={
                "select": "id,subject_id,year,session,paper_code,paper_type,file_url",
                "id": f"in.({ids_csv})",
            },
            timeout=30,
        )
        resp.raise_for_status()
        for row in resp.json():
            papers[int(row["id"])] = row
    return papers


def _fetch_subject_names(store: SupabaseStore, subject_ids: list[int]) -> dict[int, str]:
    names: dict[int, str] = {}
    for batch in _chunked(subject_ids, size=100):
        ids_csv = ",".join(str(x) for x in batch)
        resp = store.session.get(
            store._rest_url("subjects"),
            params={"select": "id,name", "id": f"in.({ids_csv})"},
            timeout=30,
        )
        resp.raise_for_status()
        for row in resp.json():
            names[int(row["id"])] = str(row["name"])
    return names


def _source_name(paper: dict, subject_name: str | None) -> str:
    subject = (subject_name or "subject").lower().replace(" ", "-")
    return (
        f"{subject}_{paper['year']}_{paper['session']}_"
        f"{paper['paper_type']}_{paper['paper_code']}.txt"
    )


def load_supabase_index(
    *,
    ms_only: bool = True,
    subject_name: str | None = None,
    page_size: int = 500,
) -> tuple[list[dict], object, object]:
    cfg = SupabaseConfig.from_env()
    store = SupabaseStore(cfg)

    text_rows = _fetch_all_paper_text_rows(store, page_size=page_size)
    if not text_rows:
        raise RuntimeError("No rows found in table paper_texts.")

    paper_ids = sorted({int(r["paper_id"]) for r in text_rows})
    papers = _fetch_papers_by_ids(store, paper_ids)
    subject_ids = sorted({int(p["subject_id"]) for p in papers.values() if p.get("subject_id") is not None})
    subject_names = _fetch_subject_names(store, subject_ids)

    subject_filter = subject_name.strip().lower() if subject_name else None
    chunks: list[dict] = []

    for row in text_rows:
        paper = papers.get(int(row["paper_id"]))
        if not paper:
            continue
        paper_type = str(paper["paper_type"]).lower()
        if ms_only and paper_type != "ms":
            continue

        paper_subject_name = subject_names.get(int(paper["subject_id"]), "")
        if subject_filter and paper_subject_name.strip().lower() != subject_filter:
            continue

        text = (row.get("text_content") or "").strip()
        if not text:
            continue
        source = _source_name(paper, paper_subject_name)
        for ch in _split_into_chunks(text):
            chunks.append(
                {
                    "text": ch["text"],
                    "source": source,
                    "qid": ch["qid"],
                    "paper_id": int(paper["id"]),
                    "paper_type": paper_type,
                }
            )

    if not chunks:
        raise RuntimeError("No usable chunks found in paper_texts for the selected filters.")

    vectorizer, matrix = build_vector_index(chunks)
    return chunks, vectorizer, matrix
