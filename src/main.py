import argparse
from pathlib import Path

from pdf_loader import load_pdf_text
from retrieval import find_best_chunks
from indexing import load_index, query_index
from supabase_index import load_supabase_index
from command_word import detect_command_word, detect_marks
from answer_formatter import format_answer
from llm_client import LLMError, generate_answer


def main() -> int:
    parser = argparse.ArgumentParser(description="CIE 9618 Paper 1 exam helper (backend CLI)")
    parser.add_argument("--question-text", required=True, help="Question text to answer")
    parser.add_argument("--qp-pdf", default="data/pdfs/paper1_qp.pdf", help="Path to question paper PDF")
    parser.add_argument("--ms-pdf", default="data/pdfs/paper1_ms.pdf", help="Path to mark scheme PDF")
    parser.add_argument("--index-dir", default="data/index", help="Path to prebuilt index directory")
    parser.add_argument("--max-chunks", type=int, default=3, help="Max mark-scheme chunks to use")
    parser.add_argument("--provider", default="groq", help="LLM provider: openai|groq|gemini|grok")
    parser.add_argument("--model", default="llama-3.3-70b-versatile", help="Model name for the provider")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM and use local formatter")
    parser.add_argument("--question-id", help="Optional question id (e.g., '2 (a)') to narrow retrieval")
    parser.add_argument("--debug", action="store_true", help="Print debug info")
    parser.add_argument(
        "--use-supabase-texts",
        action="store_true",
        help="Load retrieval corpus directly from Supabase table paper_texts",
    )
    parser.add_argument(
        "--supabase-include-qp",
        action="store_true",
        help="When using Supabase texts, include question papers (default is MS only)",
    )
    parser.add_argument(
        "--supabase-subject",
        help="Optional exact subject name filter in Supabase (e.g., 'computer-science')",
    )
    parser.add_argument(
        "--supabase-page-size",
        type=int,
        default=500,
        help="Page size used when loading rows from Supabase",
    )

    args = parser.parse_args()

    qp_path = Path(args.qp_pdf)
    ms_path = Path(args.ms_pdf)
    index_dir = Path(args.index_dir)
    has_index = (index_dir / "chunks.json").exists() and (index_dir / "index.pkl").exists()

    question_text = args.question_text.strip()
    if not question_text:
        raise SystemExit("Question text cannot be empty.")

    command_word = detect_command_word(question_text)
    marks = detect_marks(question_text)

    if args.use_supabase_texts:
        chunks_data, vectorizer, matrix = load_supabase_index(
            ms_only=not args.supabase_include_qp,
            subject_name=args.supabase_subject,
            page_size=max(1, args.supabase_page_size),
        )
        results = query_index(
            question_text,
            chunks_data,
            vectorizer,
            matrix,
            top_k=args.max_chunks,
            question_id=args.question_id,
        )
        chunks = [r["text"] for r in results]
    elif has_index:
        chunks_data, vectorizer, matrix = load_index(index_dir)
        results = query_index(
            question_text,
            chunks_data,
            vectorizer,
            matrix,
            top_k=args.max_chunks,
            question_id=args.question_id,
        )
        chunks = [r["text"] for r in results]
    else:
        if not qp_path.exists():
            raise SystemExit(f"Question paper PDF not found: {qp_path}")
        if not ms_path.exists():
            raise SystemExit(f"Mark scheme PDF not found: {ms_path}")
        ms_text = load_pdf_text(ms_path)
        chunks = find_best_chunks(question_text, ms_text, max_chunks=args.max_chunks)

    if args.no_llm:
        exact_answer, short_explanation = format_answer(
            question_text=question_text,
            command_word=command_word,
            marks=marks,
            ms_chunks=chunks,
        )
    else:
        try:
            exact_answer, short_explanation = generate_answer(
                provider=args.provider,
                model=args.model,
                question_text=question_text,
                command_word=command_word,
                marks=marks,
                ms_chunks=chunks,
            )
        except LLMError as e:
            print(f"[WARN] LLM failed: {e}. Falling back to local formatter.")
            exact_answer, short_explanation = format_answer(
                question_text=question_text,
                command_word=command_word,
                marks=marks,
                ms_chunks=chunks,
            )

    if args.debug:
        print(f"[DEBUG] command_word={command_word} marks={marks}")
        print("[DEBUG] top_chunks:")
        for i, c in enumerate(chunks, start=1):
            print(f"  {i}. {c[:200].replace('\n', ' ')}...")

    print("Exact Answer:")
    print(exact_answer)
    print("\nShort Explanation:")
    print(short_explanation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
