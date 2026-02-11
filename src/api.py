import sys
from pathlib import Path
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Ensure sibling modules under src/ are importable in both run modes:
# 1) uvicorn src.api:app
# 2) uvicorn api:app --app-dir src
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from answer_formatter import format_answer
from command_word import detect_command_word, detect_marks
from indexing import build_index, load_index, query_index
from ingest import derive_ms_url_from_qp, ingest_once
from llm_client import LLMError, generate_answer
from pdf_loader import load_pdf_text
from retrieval import find_best_chunks
from supabase_index import load_supabase_index


app = FastAPI(title="AI Exam Helper API", version="0.1.0")

cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
allowed_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnswerRequest(BaseModel):
    question_text: str = Field(..., min_length=1)
    question_id: str | None = None
    max_chunks: int = 3
    provider: str = "groq"
    model: str = "llama-3.3-70b-versatile"
    no_llm: bool = False
    index_dir: str = "data/index"
    qp_pdf: str = "data/pdfs/paper1_qp.pdf"
    ms_pdf: str = "data/pdfs/paper1_ms.pdf"
    use_supabase_texts: bool = False
    supabase_include_qp: bool = False
    supabase_subject: str | None = None
    supabase_page_size: int = 500
    debug: bool = False


class AnswerResponse(BaseModel):
    exact_answer: str
    short_explanation: str
    command_word: str
    marks: int | None
    matched_chunks: list[str] | None = None


class IngestRequest(BaseModel):
    url: str = Field(..., min_length=1)
    auto_ms: bool = False
    pdf_dir: str = "data/pdfs"
    text_dir: str = "data/text"
    upload_supabase: bool = False
    store_text_in_db: bool = False
    store_text_in_bucket: bool = False
    supabase_only: bool = False
    subject: str = "computer-science"
    bucket: str | None = None
    storage_prefix: str = ""
    year: int | None = None
    session: str | None = None
    paper_code: str | None = None
    paper_type: str | None = None
    rebuild_index: bool = False
    index_dir: str = "data/index"
    include_qp_in_index: bool = False


class IngestResponse(BaseModel):
    files: list[dict]
    rebuilt_index: dict | None = None


class RebuildIndexRequest(BaseModel):
    text_dir: str = "data/text"
    index_dir: str = "data/index"
    include_qp: bool = False


class RebuildIndexResponse(BaseModel):
    chunks_path: str
    index_path: str


def _retrieve_chunks(req: AnswerRequest) -> list[str]:
    index_dir = Path(req.index_dir)
    has_index = (index_dir / "chunks.json").exists() and (index_dir / "index.pkl").exists()

    if req.use_supabase_texts:
        chunks_data, vectorizer, matrix = load_supabase_index(
            ms_only=not req.supabase_include_qp,
            subject_name=req.supabase_subject,
            page_size=max(1, req.supabase_page_size),
        )
        rows = query_index(
            req.question_text,
            chunks_data,
            vectorizer,
            matrix,
            top_k=max(1, req.max_chunks),
            question_id=req.question_id,
        )
        return [r["text"] for r in rows]

    if has_index:
        chunks_data, vectorizer, matrix = load_index(index_dir)
        rows = query_index(
            req.question_text,
            chunks_data,
            vectorizer,
            matrix,
            top_k=max(1, req.max_chunks),
            question_id=req.question_id,
        )
        return [r["text"] for r in rows]

    qp_path = Path(req.qp_pdf)
    ms_path = Path(req.ms_pdf)
    if not qp_path.exists():
        raise HTTPException(status_code=400, detail=f"Question paper PDF not found: {qp_path}")
    if not ms_path.exists():
        raise HTTPException(status_code=400, detail=f"Mark scheme PDF not found: {ms_path}")

    ms_text = load_pdf_text(ms_path)
    return find_best_chunks(req.question_text, ms_text, max_chunks=max(1, req.max_chunks))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/answer", response_model=AnswerResponse)
def answer(req: AnswerRequest) -> AnswerResponse:
    question_text = req.question_text.strip()
    if not question_text:
        raise HTTPException(status_code=400, detail="question_text cannot be empty.")

    command_word = detect_command_word(question_text)
    marks = detect_marks(question_text)

    chunks = _retrieve_chunks(req)
    if not chunks:
        raise HTTPException(status_code=404, detail="No matching mark-scheme chunks found.")

    if req.no_llm:
        exact_answer, short_explanation = format_answer(
            question_text=question_text,
            command_word=command_word,
            marks=marks,
            ms_chunks=chunks,
        )
    else:
        try:
            exact_answer, short_explanation = generate_answer(
                provider=req.provider,
                model=req.model,
                question_text=question_text,
                command_word=command_word,
                marks=marks,
                ms_chunks=chunks,
            )
        except LLMError as err:
            exact_answer, short_explanation = format_answer(
                question_text=question_text,
                command_word=command_word,
                marks=marks,
                ms_chunks=chunks,
            )
            if req.debug:
                short_explanation = (
                    f"[LLM fallback: {err}] {short_explanation}" if short_explanation else str(err)
                )

    return AnswerResponse(
        exact_answer=exact_answer,
        short_explanation=short_explanation,
        command_word=command_word,
        marks=marks,
        matched_chunks=chunks if req.debug else None,
    )


@app.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest) -> IngestResponse:
    if req.supabase_only and not req.upload_supabase:
        raise HTTPException(status_code=400, detail="supabase_only requires upload_supabase=true")
    if req.store_text_in_db and not req.upload_supabase:
        raise HTTPException(status_code=400, detail="store_text_in_db requires upload_supabase=true")
    if req.store_text_in_bucket and not req.upload_supabase:
        raise HTTPException(
            status_code=400, detail="store_text_in_bucket requires upload_supabase=true"
        )
    if req.supabase_only and req.rebuild_index:
        raise HTTPException(status_code=400, detail="Cannot rebuild local index in supabase_only mode.")

    files: list[dict] = []
    try:
        result = ingest_once(
            url=req.url,
            pdf_dir=req.pdf_dir,
            text_dir=req.text_dir,
            upload_supabase=req.upload_supabase,
            subject=req.subject,
            bucket=req.bucket,
            storage_prefix=req.storage_prefix,
            year=req.year,
            session=req.session,
            paper_code=req.paper_code,
            paper_type=req.paper_type,
            store_text_in_db=req.store_text_in_db,
            store_text_in_bucket=req.store_text_in_bucket,
            supabase_only=req.supabase_only,
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Unknown ingest failure"))
        files.append(result)
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    if req.auto_ms:
        ms_url = derive_ms_url_from_qp(req.url)
        try:
            ms_result = ingest_once(
                url=ms_url,
                pdf_dir=req.pdf_dir,
                text_dir=req.text_dir,
                upload_supabase=req.upload_supabase,
                subject=req.subject,
                bucket=req.bucket,
                storage_prefix=req.storage_prefix,
                year=req.year,
                session=req.session,
                paper_code=req.paper_code,
                paper_type="ms",
                store_text_in_db=req.store_text_in_db,
                store_text_in_bucket=req.store_text_in_bucket,
                supabase_only=req.supabase_only,
            )
            if not ms_result.get("ok"):
                raise RuntimeError(ms_result.get("error", "Unknown MS ingest failure"))
            files.append(ms_result)
        except Exception as err:
            raise HTTPException(status_code=400, detail=f"MS ingest failed: {err}") from err

    rebuilt: dict | None = None
    if req.rebuild_index:
        text_dir = Path(req.text_dir)
        index_dir = Path(req.index_dir)
        if not text_dir.exists():
            raise HTTPException(status_code=400, detail=f"Text directory not found: {text_dir}")
        try:
            chunks_path, index_path = build_index(
                text_dir=text_dir,
                index_dir=index_dir,
                ms_only=not req.include_qp_in_index,
            )
            rebuilt = {"chunks_path": str(chunks_path), "index_path": str(index_path)}
        except Exception as err:
            raise HTTPException(status_code=400, detail=f"Index rebuild failed: {err}") from err

    return IngestResponse(files=files, rebuilt_index=rebuilt)


@app.post("/index/rebuild", response_model=RebuildIndexResponse)
def rebuild_index(req: RebuildIndexRequest) -> RebuildIndexResponse:
    text_dir = Path(req.text_dir)
    index_dir = Path(req.index_dir)
    if not text_dir.exists():
        raise HTTPException(status_code=400, detail=f"Text directory not found: {text_dir}")

    try:
        chunks_path, index_path = build_index(
            text_dir=text_dir,
            index_dir=index_dir,
            ms_only=not req.include_qp,
        )
    except Exception as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    return RebuildIndexResponse(chunks_path=str(chunks_path), index_path=str(index_path))
