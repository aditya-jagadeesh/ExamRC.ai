# AI Exam Helper (Backend CLI)

## Quick start
1) Create a virtual environment (optional but recommended)
2) Install dependencies:

```bash
pip install -r requirements.txt
```

3) Run the CLI:

```bash
python src/main.py --question-text "Explain the purpose of an ALU." --qp-pdf data/pdfs/paper1_qp.pdf --ms-pdf data/pdfs/paper1_ms.pdf
```

## Run API
Start server:
```bash
uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

Open docs:
- `http://127.0.0.1:8000/docs`

Example answer request (Supabase retrieval):
```bash
curl -X POST "http://127.0.0.1:8000/answer" ^
  -H "Content-Type: application/json" ^
  -d "{\"question_text\":\"Explain the purpose of an ALU.\",\"use_supabase_texts\":true,\"supabase_subject\":\"computer-science\",\"max_chunks\":6}"
```

Example ingest request (Supabase-only + bucket text files):
```bash
curl -X POST "http://127.0.0.1:8000/ingest" ^
  -H "Content-Type: application/json" ^
  -d "{\"url\":\"https://pastpapers.co/api/file/caie/A-Level/Computer%20Science%20(for%20first%20examination%20in%202021)%20(9618)/2025-May-June/9618_s25_qp_11.pdf\",\"auto_ms\":true,\"upload_supabase\":true,\"store_text_in_bucket\":true,\"supabase_only\":true,\"subject\":\"computer-science\"}"
```

## Deploy API
This repo is now deployment-ready as a FastAPI service.

### Option A: Deploy on Render
1. Push this repo to GitHub.
2. In Render, create a new Blueprint and select this repo.
3. Render will read `render.yaml` and create the web service.
4. Set required secrets in Render:
   - `GROQ_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `SUPABASE_BUCKET` (optional if using default)
   - `CORS_ORIGINS` (set your frontend domain)

### Option B: Deploy with Docker (any VPS/platform)
Build:
```bash
docker build -t cie-9618-api .
```

Run:
```bash
docker run -p 8000:8000 --env-file .env cie-9618-api
```

Health check:
```bash
curl http://127.0.0.1:8000/health
```

## Run API server (for frontend)
Start the backend API:

```bash
uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

Endpoints:
- `GET /health`
- `POST /answer`
- `POST /ingest`
- `POST /index/rebuild`

Example answer request:

```bash
curl -X POST "http://localhost:8000/answer" ^
  -H "Content-Type: application/json" ^
  -d "{\"question_text\":\"Explain how data is transferred using real-time bit streaming.\",\"provider\":\"groq\",\"model\":\"llama-3.3-70b-versatile\"}"
```

## Download + convert a past paper PDF
Use the ingest script to download a PDF from the pastpapers.co link and store the extracted text.

```bash
python src/ingest.py --url "https://pastpapers.co/caie/a-level/computer-science-(for-first-examination-in-2021)-(9618)/2025-may-june/9618_s25_qp_11.pdf"
```

Outputs:
- `data/pdfs/<file>.pdf`
- `data/text/<file>.txt`
- `data/text/<file>.json` (metadata)

## Supabase storage + metadata sync
If you want ingest to also push PDFs to Supabase Storage and insert a row in your `subjects` and `papers` tables:

1) Set environment variables in `.env`:
```
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SERVICE_KEY=YOUR_SERVICE_ROLE_KEY
SUPABASE_BUCKET=past-papers
```

2) Run ingest with Supabase upload enabled:
```bash
python src/ingest.py --url "https://pastpapers.co/api/file/caie/A-Level/Computer%20Science%20(for%20first%20examination%20in%202021)%20(9618)/2025-May-June/9618_s25_qp_11.pdf" --upload-supabase --subject "computer-science"
```

To also store extracted text in Supabase Postgres:
1) Run SQL in `sql/supabase_paper_texts.sql`
2) Add `--store-text-in-db`:

```bash
python src/ingest.py --url "https://...pdf" --upload-supabase --store-text-in-db --subject "computer-science"
```

Default object path format inside the bucket:
- `<subject>/<year>/<session>/<paper_code>/<paper_type>.pdf`
- Example: `computer-science/2025/s/11/qp.pdf`

Optional overrides when filename parsing is unavailable:
- `--year`, `--session`, `--paper-code`, `--paper-type`
- `--bucket` to override bucket
- `--storage-prefix` to prepend a folder (e.g., `cie/9618`)

## Download QP + auto-derive MS
If the URL is a QP and follows the common pattern `*_qp_*.pdf`, you can auto-download the MS:

```bash
python src/ingest.py --url "https://pastpapers.co/api/file/caie/A-Level/Computer%20Science%20(for%20first%20examination%20in%202021)%20(9618)/2025-May-June/9618_s25_qp_11.pdf" --auto-ms
```

## Build a searchable index
After ingesting multiple papers, build the TF-IDF index:

```bash
python src/build_index.py --text-dir data/text --index-dir data/index
```

Include question papers too (not recommended for answer quality):

```bash
python src/build_index.py --text-dir data/text --index-dir data/index --include-qp
```

Then run the CLI using the index:

```bash
python src/main.py --question-text "Explain how data is transferred using real-time bit streaming."
```

Use Supabase as retrieval source (no local index required):
```bash
python src/main.py --question-text "Explain the purpose of an ALU." --use-supabase-texts --supabase-subject "computer-science"
```

Options for Supabase retrieval:
- `--supabase-include-qp` to include question papers (default is MS only)
- `--supabase-page-size 500` to tune pagination size

Debug / narrow to a question id (when known):

```bash
python src/main.py --question-text "Explain how data is transferred using real-time bit streaming." --question-id "2 (a)" --debug
```

## One-command ingest + index
You can now build/update the index directly from `ingest.py`:

```bash
python src/ingest.py --url "https://.../9618_s25_qp_11.pdf" --auto-ms --build-index
```

Useful options:
- `--index-dir data/index`
- `--include-qp-in-index` (default keeps index MS-only)

Bulk download (2021 to 2025, sessions m/s/w, Paper 1 variants 11/12/13, both qp+ms):

```bash
python src/ingest.py --bulk-years 2021-2025 --build-index --upload-supabase --subject "computer-science"
```

Bulk mode options:
- `--bulk-sessions m,s,w`
- `--bulk-paper-codes 11,12,13`
- `--bulk-types qp,ms`
- `--workers 4`
- Add `--store-text-in-db` to persist extracted text/metadata in `paper_texts`

## Backfill bucket files from DB
If you already stored extracted text in `paper_texts` and want `.txt/.json` objects in the bucket:

Dry run:
```bash
python src/backfill_bucket_from_db.py --dry-run
```

Apply:
```bash
python src/backfill_bucket_from_db.py
```

## LLM formatting (OpenAI/Groq/Gemini/Grok)
The CLI can use a provider to format the final answer using the retrieved mark scheme chunks.

OpenAI (supported):
- Set `OPENAI_API_KEY`
- Optional: `OPENAI_BASE_URL` (defaults to `https://api.openai.com/v1/responses`)
 - Optional: `OPENAI_TIMEOUT` (seconds, default `60`)
 - Optional: `OPENAI_MAX_RETRIES` (default `3`)
 - Optional: `OPENAI_RETRY_BACKOFF` (seconds multiplier, default `1.5`)

Example:
```bash
setx OPENAI_API_KEY "YOUR_KEY"
python src/main.py --question-text "Explain how data is transferred using real-time bit streaming." --provider openai --model gpt-4.1-mini
```

You can also use a local `.env` file in the project root:
```
OPENAI_API_KEY=YOUR_KEY
OPENAI_TIMEOUT=60
OPENAI_MAX_RETRIES=3
OPENAI_RETRY_BACKOFF=1.5
```

Groq (supported):
- Set `GROQ_API_KEY`
- Optional: `GROQ_BASE_URL` (defaults to `https://api.groq.com/openai/v1/responses`)
- Optional: `GROQ_TIMEOUT` (seconds, default `60`)
- Optional: `GROQ_MAX_RETRIES` (default `3`)
- Optional: `GROQ_RETRY_BACKOFF` (seconds multiplier, default `1.5`)

Example (default provider):
```bash
setx GROQ_API_KEY "YOUR_KEY"
python src/main.py --question-text "Explain how data is transferred using real-time bit streaming." --model llama-3.3-70b-versatile
```

`.env` example:
```
GROQ_API_KEY=YOUR_KEY
GROQ_TIMEOUT=60
GROQ_MAX_RETRIES=3
GROQ_RETRY_BACKOFF=1.5
```

Disable LLM (fallback to local formatter):
```bash
python src/main.py --question-text "Explain how data is transferred using real-time bit streaming." --no-llm
```

## Output format
The tool returns two parts:
- Exact Answer (mark-scheme style, keyword-focused)
- Short Explanation (brief clarification)

## Notes
This is a backend-first scaffold. The retrieval and formatting logic is intentionally simple and will be upgraded later (e.g., vector search, LLM synthesis).
#
