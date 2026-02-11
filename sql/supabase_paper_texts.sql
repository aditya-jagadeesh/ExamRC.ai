-- Stores extracted text/metadata in Postgres so content is not only in local files.
-- Run this once in the Supabase SQL editor.

CREATE TABLE IF NOT EXISTS paper_texts (
  id BIGSERIAL PRIMARY KEY,
  paper_id INTEGER UNIQUE NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  source_url TEXT,
  text_content TEXT NOT NULL,
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMP DEFAULT now(),
  updated_at TIMESTAMP DEFAULT now()
);

-- Optional trigger to keep updated_at fresh on updates.
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = now();
   RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS trg_paper_texts_updated_at ON paper_texts;
CREATE TRIGGER trg_paper_texts_updated_at
BEFORE UPDATE ON paper_texts
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
