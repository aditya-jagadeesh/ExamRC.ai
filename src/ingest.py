import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

from indexing import build_index
from pdf_loader import load_pdf_text, load_pdf_text_from_bytes
from supabase_store import (
    SupabaseConfig,
    SupabaseStore,
    build_default_storage_path,
    parse_paper_meta_from_stem,
)

SESSION_FOLDER = {
    "m": "Feb-March",
    "s": "May-June",
    "w": "Oct-Nov",
}


def _safe_stem(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[^\w\-\.]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "document"


def _download_pdf_bytes(url: str) -> tuple[str, bytes]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            "Invalid --url value. Provide a full http(s) PDF URL, "
            "for example: https://example.com/paper.pdf"
        )

    filename = url.rstrip("/").split("/")[-1]
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    stem = _safe_stem(Path(filename).stem)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    resp = requests.get(url, timeout=30, headers=headers, allow_redirects=True)
    resp.raise_for_status()
    content = resp.content
    if not content.startswith(b"%PDF"):
        raise RuntimeError("Download did not return a valid PDF.")
    return stem, content


def _write_text(text: str, out_dir: Path, stem: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / f"{stem}.txt"
    txt_path.write_text(text, encoding="utf-8")
    return txt_path


def _write_meta(out_dir: Path, stem: str, source_url: str, pdf_path: Path, txt_path: Path) -> Path:
    meta = {
        "source_url": source_url,
        "pdf_path": str(pdf_path),
        "text_path": str(txt_path),
    }
    meta_path = out_dir / f"{stem}.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta_path


def ingest_from_url(url: str, pdf_dir: Path, text_dir: Path) -> tuple[Path, Path, Path]:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    stem, pdf_bytes = _download_pdf_bytes(url)
    pdf_path = pdf_dir / f"{stem}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    text = load_pdf_text(pdf_path)
    txt_path = _write_text(text, text_dir, stem)
    meta_path = _write_meta(text_dir, stem, url, pdf_path, txt_path)
    return pdf_path, txt_path, meta_path


def derive_ms_url_from_qp(qp_url: str) -> str:
    return re.sub(r"_qp_", "_ms_", qp_url)


def _build_caie_9618_url(
    year: int,
    session: str,
    paper_type: str,
    paper_code: str,
    subject_code: str = "9618",
) -> str:
    if session not in SESSION_FOLDER:
        raise RuntimeError(f"Unsupported session '{session}'. Use one of: m,s,w.")
    yy = str(year)[-2:]
    session_folder = SESSION_FOLDER[session]
    return (
        "https://pastpapers.co/api/file/caie/A-Level/"
        "Computer%20Science%20(for%20first%20examination%20in%202021)%20(9618)/"
        f"{year}-{session_folder}/{subject_code}_{session}{yy}_{paper_type}_{paper_code}.pdf"
    )


def _resolve_paper_meta(
    stem: str,
    year: int | None,
    session: str | None,
    paper_code: str | None,
    paper_type: str | None,
) -> tuple[int, str, str, str]:
    parsed = parse_paper_meta_from_stem(stem)
    resolved_year = year if year is not None else parsed.get("year")
    resolved_session = (session or parsed.get("session") or "").strip().lower()
    resolved_paper_code = (paper_code or parsed.get("paper_code") or "").strip()
    resolved_paper_type = (paper_type or parsed.get("paper_type") or "").strip().lower()

    if resolved_year is None:
        raise RuntimeError(f"Could not infer year from filename '{stem}'. Provide --year.")
    if not resolved_session:
        raise RuntimeError(f"Could not infer session from filename '{stem}'. Provide --session.")
    if not resolved_paper_code:
        raise RuntimeError(f"Could not infer paper code from filename '{stem}'. Provide --paper-code.")
    if resolved_paper_type not in {"qp", "ms"}:
        raise RuntimeError(
            f"Could not infer valid paper type from filename '{stem}'. Provide --paper-type qp|ms."
        )

    return int(resolved_year), resolved_session, resolved_paper_code, resolved_paper_type


def _upload_and_record_supabase(
    *,
    pdf_bytes: bytes,
    stem: str,
    source_url: str,
    text_content: str,
    metadata: dict,
    subject: str,
    bucket: str | None,
    storage_prefix: str,
    year: int | None,
    session: str | None,
    paper_code: str | None,
    paper_type: str | None,
    store_text_in_db: bool,
    store_text_in_bucket: bool,
) -> dict:
    cfg = SupabaseConfig.from_env(bucket_override=bucket)
    store = SupabaseStore(cfg)

    resolved_year, resolved_session, resolved_code, resolved_type = _resolve_paper_meta(
        stem=stem,
        year=year,
        session=session,
        paper_code=paper_code,
        paper_type=paper_type,
    )
    base_object = build_default_storage_path(
        subject=subject,
        year=resolved_year,
        session=resolved_session,
        paper_code=resolved_code,
        paper_type=resolved_type,
    )
    prefix = storage_prefix.strip().strip("/")
    object_path = f"{prefix}/{base_object}" if prefix else base_object

    file_url = store.upload_pdf_bytes(pdf_bytes, object_path=object_path, upsert=True)
    subject_id = store.ensure_subject(subject)
    paper_row = store.insert_paper(
        subject_id=subject_id,
        year=resolved_year,
        session=resolved_session,
        paper_code=resolved_code,
        paper_type=resolved_type,
        file_url=file_url,
    )
    output = {
        "bucket": cfg.bucket,
        "object_path": object_path,
        "file_url": file_url,
        "paper_id": paper_row["id"],
    }
    if store_text_in_bucket:
        text_object_path = re.sub(r"\.pdf$", ".txt", object_path, flags=re.IGNORECASE)
        meta_object_path = re.sub(r"\.pdf$", ".json", object_path, flags=re.IGNORECASE)
        text_url = store.upload_bytes(
            data=text_content.encode("utf-8"),
            object_path=text_object_path,
            content_type="text/plain; charset=utf-8",
            upsert=True,
        )
        meta_url = store.upload_bytes(
            data=json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
            object_path=meta_object_path,
            content_type="application/json",
            upsert=True,
        )
        output["text_object_path"] = text_object_path
        output["meta_object_path"] = meta_object_path
        output["text_url"] = text_url
        output["meta_url"] = meta_url
    if store_text_in_db:
        text_row = store.upsert_paper_text(
            paper_id=paper_row["id"],
            text_content=text_content,
            source_url=source_url,
            metadata=metadata,
        )
        output["paper_text_id"] = text_row["id"]
    return output


def _parse_csv_arg(raw: str | list[str]) -> list[str]:
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            parts.extend(item.split(","))
        return [p.strip() for p in parts if p.strip()]
    return [p.strip() for p in raw.split(",") if p.strip()]


def _ingest_and_optional_upload(
    *,
    url: str,
    pdf_dir: Path,
    text_dir: Path,
    upload_supabase: bool,
    subject: str,
    bucket: str | None,
    storage_prefix: str,
    year: int | None,
    session: str | None,
    paper_code: str | None,
    paper_type: str | None,
    store_text_in_db: bool,
    store_text_in_bucket: bool,
    supabase_only: bool,
) -> dict:
    try:
        if supabase_only:
            stem, pdf_bytes = _download_pdf_bytes(url)
            text_content = load_pdf_text_from_bytes(pdf_bytes)
            meta = {"source_url": url, "stem": stem, "mode": "supabase_only"}
            result = {
                "ok": True,
                "url": url,
                "stem": stem,
            }
            if upload_supabase:
                upload = _upload_and_record_supabase(
                    pdf_bytes=pdf_bytes,
                    stem=stem,
                    source_url=url,
                    text_content=text_content,
                    metadata=meta,
                    subject=subject,
                    bucket=bucket,
                    storage_prefix=storage_prefix,
                    year=year,
                    session=session,
                    paper_code=paper_code,
                    paper_type=paper_type,
                    store_text_in_db=store_text_in_db,
                    store_text_in_bucket=store_text_in_bucket,
                )
                result["upload"] = upload
            return result

        pdf_path, txt_path, meta_path = ingest_from_url(url, pdf_dir, text_dir)
        result = {
            "ok": True,
            "url": url,
            "pdf_path": str(pdf_path),
            "txt_path": str(txt_path),
            "meta_path": str(meta_path),
        }
        if upload_supabase:
            upload = _upload_and_record_supabase(
                pdf_bytes=pdf_path.read_bytes(),
                stem=pdf_path.stem,
                source_url=url,
                text_content=txt_path.read_text(encoding="utf-8", errors="ignore"),
                metadata=json.loads(meta_path.read_text(encoding="utf-8")),
                subject=subject,
                bucket=bucket,
                storage_prefix=storage_prefix,
                year=year,
                session=session,
                paper_code=paper_code,
                paper_type=paper_type,
                store_text_in_db=store_text_in_db,
                store_text_in_bucket=store_text_in_bucket,
            )
            result["upload"] = upload
        return result
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)}


def ingest_once(
    *,
    url: str,
    pdf_dir: str = "data/pdfs",
    text_dir: str = "data/text",
    upload_supabase: bool = False,
    subject: str = "computer-science",
    bucket: str | None = None,
    storage_prefix: str = "",
    year: int | None = None,
    session: str | None = None,
    paper_code: str | None = None,
    paper_type: str | None = None,
    store_text_in_db: bool = False,
    store_text_in_bucket: bool = False,
    supabase_only: bool = False,
) -> dict:
    return _ingest_and_optional_upload(
        url=url,
        pdf_dir=Path(pdf_dir),
        text_dir=Path(text_dir),
        upload_supabase=upload_supabase,
        subject=subject,
        bucket=bucket,
        storage_prefix=storage_prefix,
        year=year,
        session=session,
        paper_code=paper_code,
        paper_type=paper_type,
        store_text_in_db=store_text_in_db,
        store_text_in_bucket=store_text_in_bucket,
        supabase_only=supabase_only,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Download CAIE past papers and convert to text")
    parser.add_argument("--url", help="PDF URL to download")
    parser.add_argument("--pdf-dir", default="data/pdfs", help="Where to store downloaded PDFs")
    parser.add_argument("--text-dir", default="data/text", help="Where to store extracted text")
    parser.add_argument("--auto-ms", action="store_true", help="Also download MS derived from QP URL")
    parser.add_argument("--bulk-years", help="Year range for bulk mode, e.g. 2021-2025")
    parser.add_argument(
        "--bulk-sessions",
        nargs="+",
        default=["m,s,w"],
        help="Comma-separated sessions for bulk mode (default: m,s,w)",
    )
    parser.add_argument(
        "--bulk-paper-codes",
        nargs="+",
        default=["11,12,13"],
        help="Comma-separated paper codes for bulk mode (default: 11,12,13)",
    )
    parser.add_argument(
        "--bulk-types",
        nargs="+",
        default=["qp,ms"],
        help="Comma-separated types for bulk mode (default: qp,ms)",
    )
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers for bulk ingest")
    parser.add_argument("--build-index", action="store_true", help="Build/update TF-IDF index")
    parser.add_argument("--index-dir", default="data/index", help="Index output directory")
    parser.add_argument("--include-qp-in-index", action="store_true", help="Include QP in index")
    parser.add_argument("--upload-supabase", action="store_true", help="Upload and insert metadata")
    parser.add_argument(
        "--store-text-in-db",
        action="store_true",
        help="Store extracted text+metadata in Supabase table paper_texts",
    )
    parser.add_argument(
        "--store-text-in-bucket",
        action="store_true",
        help="Store extracted .txt and .json files in Supabase Storage bucket",
    )
    parser.add_argument(
        "--supabase-only",
        action="store_true",
        help="Do not write local files; process and store in Supabase only",
    )
    parser.add_argument("--subject", default="computer-science", help="Subject name for DB table")
    parser.add_argument("--bucket", help="Optional Supabase bucket override")
    parser.add_argument("--storage-prefix", default="", help="Optional prefix in bucket")
    parser.add_argument("--year", type=int, help="Override year")
    parser.add_argument("--session", help="Override session code")
    parser.add_argument("--paper-code", help="Override paper code")
    parser.add_argument("--paper-type", choices=["qp", "ms"], help="Override paper type")
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    text_dir = Path(args.text_dir)

    if not args.url and not args.bulk_years:
        raise SystemExit("Provide either --url or --bulk-years.")
    if args.url and args.bulk_years:
        raise SystemExit("Use either --url or --bulk-years, not both.")
    if args.store_text_in_db and not args.upload_supabase:
        raise SystemExit("--store-text-in-db requires --upload-supabase.")
    if args.store_text_in_bucket and not args.upload_supabase:
        raise SystemExit("--store-text-in-bucket requires --upload-supabase.")
    if args.supabase_only and not args.upload_supabase:
        raise SystemExit("--supabase-only requires --upload-supabase.")
    if args.supabase_only and args.build_index:
        raise SystemExit("--supabase-only cannot be used with --build-index.")

    if args.bulk_years:
        m = re.fullmatch(r"\s*(\d{4})\s*-\s*(\d{4})\s*", args.bulk_years)
        if not m:
            raise SystemExit("Invalid --bulk-years format. Use: 2021-2025")
        start_year = int(m.group(1))
        end_year = int(m.group(2))
        if start_year > end_year:
            raise SystemExit("--bulk-years start must be <= end.")

        sessions = [s.lower() for s in _parse_csv_arg(args.bulk_sessions)]
        paper_codes = _parse_csv_arg(args.bulk_paper_codes)
        paper_types = [t.lower() for t in _parse_csv_arg(args.bulk_types)]

        invalid_sessions = [s for s in sessions if s not in SESSION_FOLDER]
        if invalid_sessions:
            raise SystemExit(f"Invalid sessions: {','.join(invalid_sessions)}. Allowed: m,s,w.")
        invalid_types = [t for t in paper_types if t not in {"qp", "ms"}]
        if invalid_types:
            raise SystemExit(f"Invalid bulk types: {','.join(invalid_types)}. Allowed: qp,ms.")

        jobs: list[tuple[str, int, str, str, str]] = []
        for year in range(start_year, end_year + 1):
            for session in sessions:
                for paper_code in paper_codes:
                    for paper_type in paper_types:
                        jobs.append(
                            (
                                _build_caie_9618_url(year, session, paper_type, paper_code),
                                year,
                                session,
                                paper_code,
                                paper_type,
                            )
                        )

        print(f"Bulk ingest targets: {len(jobs)}")
        ok_count = 0
        fail_count = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [
                pool.submit(
                    _ingest_and_optional_upload,
                    url=url,
                    pdf_dir=pdf_dir,
                    text_dir=text_dir,
                    upload_supabase=args.upload_supabase,
                    subject=args.subject,
                    bucket=args.bucket,
                    storage_prefix=args.storage_prefix,
                    year=year,
                    session=session,
                    paper_code=paper_code,
                    paper_type=paper_type,
                    store_text_in_db=args.store_text_in_db,
                    store_text_in_bucket=args.store_text_in_bucket,
                    supabase_only=args.supabase_only,
                )
                for url, year, session, paper_code, paper_type in jobs
            ]
            for fut in as_completed(futures):
                result = fut.result()
                if result["ok"]:
                    ok_count += 1
                    if args.supabase_only:
                        print(f"[OK] {result['stem']}")
                    else:
                        print(f"[OK] {result['pdf_path']}")
                    if "upload" in result:
                        up = result["upload"]
                        print(f"  Uploaded: {up['bucket']}/{up['object_path']} (paper_id={up['paper_id']})")
                        if "text_object_path" in up:
                            print(f"  Bucket text: {up['text_object_path']}")
                            print(f"  Bucket meta: {up['meta_object_path']}")
                        if "paper_text_id" in up:
                            print(f"  Text row: paper_texts.id={up['paper_text_id']}")
                else:
                    fail_count += 1
                    print(f"[SKIP] {result['url']} -> {result['error']}")
        print(f"Bulk ingest complete. success={ok_count} skipped={fail_count}")
    else:
        result = _ingest_and_optional_upload(
            url=args.url,
            pdf_dir=pdf_dir,
            text_dir=text_dir,
            upload_supabase=args.upload_supabase,
            subject=args.subject,
            bucket=args.bucket,
            storage_prefix=args.storage_prefix,
            year=args.year,
            session=args.session,
            paper_code=args.paper_code,
            paper_type=args.paper_type,
            store_text_in_db=args.store_text_in_db,
            store_text_in_bucket=args.store_text_in_bucket,
            supabase_only=args.supabase_only,
        )
        if not result["ok"]:
            raise SystemExit(result["error"])
        if args.supabase_only:
            print(f"Processed: {result['stem']}")
        else:
            print(f"Saved PDF: {result['pdf_path']}")
            print(f"Saved text: {result['txt_path']}")
            print(f"Saved meta: {result['meta_path']}")
        if "upload" in result:
            up = result["upload"]
            print(f"Uploaded to Supabase: {up['bucket']}/{up['object_path']}")
            print(f"Paper row id: {up['paper_id']}")
            print(f"Public URL: {up['file_url']}")
            if "text_url" in up:
                print(f"Text URL: {up['text_url']}")
                print(f"Meta URL: {up['meta_url']}")
            if "paper_text_id" in up:
                print(f"Text row id: {up['paper_text_id']}")

        if args.auto_ms:
            ms_url = derive_ms_url_from_qp(args.url)
            ms_result = _ingest_and_optional_upload(
                url=ms_url,
                pdf_dir=pdf_dir,
                text_dir=text_dir,
                upload_supabase=args.upload_supabase,
                subject=args.subject,
                bucket=args.bucket,
                storage_prefix=args.storage_prefix,
                year=args.year,
                session=args.session,
                paper_code=args.paper_code,
                paper_type="ms",
                store_text_in_db=args.store_text_in_db,
                store_text_in_bucket=args.store_text_in_bucket,
                supabase_only=args.supabase_only,
            )
            if not ms_result["ok"]:
                raise SystemExit(ms_result["error"])
            if args.supabase_only:
                print(f"Processed MS: {ms_result['stem']}")
            else:
                print(f"Saved MS PDF: {ms_result['pdf_path']}")
                print(f"Saved MS text: {ms_result['txt_path']}")
                print(f"Saved MS meta: {ms_result['meta_path']}")
            if "upload" in ms_result:
                ms_up = ms_result["upload"]
                print(f"Uploaded MS to Supabase: {ms_up['bucket']}/{ms_up['object_path']}")
                print(f"MS paper row id: {ms_up['paper_id']}")
                print(f"MS public URL: {ms_up['file_url']}")
                if "text_url" in ms_up:
                    print(f"MS text URL: {ms_up['text_url']}")
                    print(f"MS meta URL: {ms_up['meta_url']}")
                if "paper_text_id" in ms_up:
                    print(f"MS text row id: {ms_up['paper_text_id']}")

    if args.build_index:
        index_dir = Path(args.index_dir)
        ms_only = not args.include_qp_in_index
        data_path, model_path = build_index(text_dir, index_dir, ms_only=ms_only)
        print(f"Saved chunks: {data_path}")
        print(f"Saved index: {model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
