import argparse
import json

from supabase_store import SupabaseConfig, SupabaseStore, build_default_storage_path


def _extract_object_path(file_url: str, bucket: str) -> str | None:
    marker = f"/storage/v1/object/public/{bucket}/"
    if marker not in file_url:
        return None
    return file_url.split(marker, 1)[1].strip()


def _fetch_rows(store: SupabaseStore, page_size: int) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        resp = store.session.get(
            store._rest_url("paper_texts"),
            params={
                "select": "id,paper_id,source_url,text_content,metadata",
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


def _fetch_papers(store: SupabaseStore, paper_ids: list[int]) -> dict[int, dict]:
    papers: dict[int, dict] = {}
    chunk_size = 100
    for i in range(0, len(paper_ids), chunk_size):
        chunk = paper_ids[i : i + chunk_size]
        id_list = ",".join(str(x) for x in chunk)
        resp = store.session.get(
            store._rest_url("papers"),
            params={
                "select": "id,subject_id,year,session,paper_code,paper_type,file_url",
                "id": f"in.({id_list})",
            },
            timeout=30,
        )
        resp.raise_for_status()
        for row in resp.json():
            papers[row["id"]] = row
    return papers


def _fetch_subject_names(store: SupabaseStore, subject_ids: list[int]) -> dict[int, str]:
    if not subject_ids:
        return {}
    names: dict[int, str] = {}
    chunk_size = 100
    for i in range(0, len(subject_ids), chunk_size):
        chunk = subject_ids[i : i + chunk_size]
        id_list = ",".join(str(x) for x in chunk)
        resp = store.session.get(
            store._rest_url("subjects"),
            params={"select": "id,name", "id": f"in.({id_list})"},
            timeout=30,
        )
        resp.raise_for_status()
        for row in resp.json():
            names[row["id"]] = row["name"]
    return names


def _upload_sidecars(
    store: SupabaseStore,
    object_path: str,
    text_content: str,
    metadata: dict,
    upsert: bool,
    dry_run: bool,
) -> tuple[str, str]:
    if object_path.lower().endswith(".pdf"):
        txt_path = object_path[:-4] + ".txt"
        json_path = object_path[:-4] + ".json"
    else:
        txt_path = object_path + ".txt"
        json_path = object_path + ".json"

    if dry_run:
        return txt_path, json_path

    store.upload_bytes(
        data=text_content.encode("utf-8"),
        object_path=txt_path,
        content_type="text/plain; charset=utf-8",
        upsert=upsert,
    )
    store.upload_bytes(
        data=json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        object_path=json_path,
        content_type="application/json",
        upsert=upsert,
    )
    return txt_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Supabase bucket .txt/.json files from paper_texts rows"
    )
    parser.add_argument("--bucket", help="Optional bucket override")
    parser.add_argument(
        "--fallback-subject",
        default="computer-science",
        help="Used if paper file_url is missing and subject lookup fails",
    )
    parser.add_argument("--page-size", type=int, default=500, help="Rows per page")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without uploading")
    parser.add_argument(
        "--no-upsert",
        action="store_true",
        help="Do not overwrite existing bucket objects",
    )
    args = parser.parse_args()

    cfg = SupabaseConfig.from_env(bucket_override=args.bucket)
    store = SupabaseStore(cfg)
    upsert = not args.no_upsert

    text_rows = _fetch_rows(store, page_size=max(1, args.page_size))
    if not text_rows:
        print("No rows found in paper_texts.")
        return 0

    paper_ids = sorted({int(r["paper_id"]) for r in text_rows})
    papers = _fetch_papers(store, paper_ids)
    subject_ids = sorted({int(p["subject_id"]) for p in papers.values() if p.get("subject_id") is not None})
    subject_names = _fetch_subject_names(store, subject_ids)

    ok = 0
    skipped = 0
    failed = 0
    for row in text_rows:
        paper_id = int(row["paper_id"])
        paper = papers.get(paper_id)
        if not paper:
            skipped += 1
            print(f"[SKIP] paper_id={paper_id}: missing papers row")
            continue

        object_path = None
        if paper.get("file_url"):
            object_path = _extract_object_path(str(paper["file_url"]), cfg.bucket)

        if not object_path:
            subject_name = subject_names.get(int(paper["subject_id"]), args.fallback_subject)
            object_path = build_default_storage_path(
                subject=subject_name,
                year=int(paper["year"]),
                session=str(paper["session"]),
                paper_code=str(paper["paper_code"]),
                paper_type=str(paper["paper_type"]),
            )

        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {"value": metadata}
        if row.get("source_url"):
            metadata.setdefault("source_url", row["source_url"])
        metadata.setdefault("paper_text_id", row["id"])
        metadata.setdefault("paper_id", paper_id)

        text_content = row.get("text_content") or ""
        if not text_content.strip():
            skipped += 1
            print(f"[SKIP] paper_text_id={row['id']}: empty text_content")
            continue

        try:
            txt_path, json_path = _upload_sidecars(
                store=store,
                object_path=object_path,
                text_content=text_content,
                metadata=metadata,
                upsert=upsert,
                dry_run=args.dry_run,
            )
            ok += 1
            mode = "DRY" if args.dry_run else "OK"
            print(f"[{mode}] paper_text_id={row['id']} -> {txt_path} | {json_path}")
        except Exception as e:
            failed += 1
            print(f"[FAIL] paper_text_id={row['id']}: {e}")

    print(f"Done. processed={ok} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
