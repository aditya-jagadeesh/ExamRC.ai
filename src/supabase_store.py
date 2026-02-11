import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests


FILENAME_RE = re.compile(
    r"(?P<subject_code>\d{4})_(?P<session>[a-z])(?P<year>\d{2})_(?P<paper_type>qp|ms)_(?P<paper_code>\d+)",
    re.IGNORECASE,
)


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _ensure_env_loaded() -> None:
    _load_env_file(Path.cwd() / ".env")


@dataclass
class SupabaseConfig:
    url: str
    service_key: str
    bucket: str

    @classmethod
    def from_env(cls, bucket_override: str | None = None) -> "SupabaseConfig":
        _ensure_env_loaded()
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        service_key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
        bucket = (bucket_override or os.getenv("SUPABASE_BUCKET", "past-papers")).strip()

        if not url:
            raise RuntimeError("SUPABASE_URL is not set.")
        if not service_key:
            raise RuntimeError("SUPABASE_SERVICE_KEY is not set.")
        if not bucket:
            raise RuntimeError("Supabase bucket name is empty.")
        return cls(url=url, service_key=service_key, bucket=bucket)


def parse_paper_meta_from_stem(stem: str) -> dict:
    m = FILENAME_RE.search(stem)
    if not m:
        return {}
    year = 2000 + int(m.group("year"))
    return {
        "year": year,
        "session": m.group("session").lower(),
        "paper_type": m.group("paper_type").lower(),
        "paper_code": m.group("paper_code"),
        "subject_code": m.group("subject_code"),
    }


class SupabaseStore:
    def __init__(self, config: SupabaseConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "apikey": self.config.service_key,
                "Authorization": f"Bearer {self.config.service_key}",
            }
        )

    def _rest_url(self, table: str) -> str:
        return f"{self.config.url}/rest/v1/{table}"

    def _public_object_url(self, object_path: str) -> str:
        return f"{self.config.url}/storage/v1/object/public/{self.config.bucket}/{object_path}"

    def ensure_subject(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise RuntimeError("Subject name cannot be empty.")

        select_url = self._rest_url("subjects")
        params = {"name": f"eq.{name}", "select": "id"}
        resp = self.session.get(select_url, params=params, timeout=30)
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            return rows[0]["id"]

        insert_headers = {"Prefer": "return=representation"}
        insert_resp = self.session.post(
            select_url,
            headers=insert_headers,
            json={"name": name},
            timeout=30,
        )
        if insert_resp.status_code == 409:
            resp = self.session.get(select_url, params=params, timeout=30)
            resp.raise_for_status()
            rows = resp.json()
            if rows:
                return rows[0]["id"]
            raise RuntimeError("Subject exists but could not be reloaded.")

        insert_resp.raise_for_status()
        created = insert_resp.json()
        if not created:
            raise RuntimeError("Failed to create subject row.")
        return created[0]["id"]

    def upload_pdf(self, file_path: Path, object_path: str, upsert: bool = True) -> str:
        data = file_path.read_bytes()
        return self.upload_pdf_bytes(data, object_path=object_path, upsert=upsert)

    def upload_pdf_bytes(self, data: bytes, object_path: str, upsert: bool = True) -> str:
        return self.upload_bytes(
            data=data,
            object_path=object_path,
            content_type="application/pdf",
            upsert=upsert,
        )

    def upload_bytes(
        self,
        data: bytes,
        object_path: str,
        content_type: str,
        upsert: bool = True,
    ) -> str:
        object_path = object_path.strip().lstrip("/")
        if not object_path:
            raise RuntimeError("Supabase storage object path cannot be empty.")

        upload_url = (
            f"{self.config.url}/storage/v1/object/"
            f"{self.config.bucket}/{quote(object_path, safe='/')}"
        )
        headers = {
            "Content-Type": content_type,
            "x-upsert": "true" if upsert else "false",
        }
        resp = self.session.post(upload_url, headers=headers, data=data, timeout=60)
        resp.raise_for_status()
        return self._public_object_url(object_path)

    def insert_paper(
        self,
        subject_id: int,
        year: int,
        session: str,
        paper_code: str,
        paper_type: str,
        file_url: str,
    ) -> dict:
        payload = {
            "subject_id": subject_id,
            "year": int(year),
            "session": session,
            "paper_code": paper_code,
            "paper_type": paper_type,
            "file_url": file_url,
        }
        resp = self.session.post(
            self._rest_url("papers"),
            headers={"Prefer": "return=representation"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise RuntimeError("Paper insert returned no data.")
        return rows[0]

    def upsert_paper_text(
        self,
        paper_id: int,
        text_content: str,
        source_url: str,
        metadata: dict,
    ) -> dict:
        payload = {
            "paper_id": int(paper_id),
            "text_content": text_content,
            "source_url": source_url,
            "metadata": metadata,
        }
        resp = self.session.post(
            self._rest_url("paper_texts"),
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                "Failed to upsert paper_texts. Ensure table exists and has a UNIQUE "
                f"constraint on paper_id. Supabase error: {resp.status_code} {resp.text}"
            )
        rows = resp.json()
        if not rows:
            raise RuntimeError("paper_texts upsert returned no data.")
        return rows[0]


def build_default_storage_path(
    subject: str, year: int, session: str, paper_code: str, paper_type: str
) -> str:
    subject_slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-") or "subject"
    return f"{subject_slug}/{year}/{session}/{paper_code}/{paper_type}.pdf"
