import os
import re
import time
from pathlib import Path
from typing import Iterable

import requests


class LLMError(RuntimeError):
    pass


def _build_prompt(
    question_text: str, command_word: str, marks: int | None, ms_chunks: Iterable[str]
) -> str:
    marks_text = str(marks) if marks is not None else "unspecified"
    ms_text = "\n\n".join(ms_chunks).strip()

    return (
        "You are an exam-marking assistant for CIE A Level Computer Science (9618).\n"
        "Use ONLY the provided mark scheme content. Do not invent facts.\n"
        "Return exactly two sections with these headings:\n"
        "Exact Answer:\n"
        "Short Explanation:\n\n"
        f"Command word: {command_word}\n"
        f"Marks: {marks_text}\n"
        f"Question: {question_text}\n\n"
        "Mark Scheme Content:\n"
        f"{ms_text}\n"
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
    # Load .env from project root if present
    cwd = Path.cwd()
    _load_env_file(cwd / ".env")


def _extract_output_text(resp_json: dict) -> str:
    # Prefer SDK-style aggregate if present.
    if "output_text" in resp_json and isinstance(resp_json["output_text"], str):
        return resp_json["output_text"]

    # Otherwise, walk the output array for output_text blocks.
    output = resp_json.get("output", [])
    texts: list[str] = []
    for item in output:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                texts.append(content.get("text", ""))
    return "\n".join(t for t in texts if t)


def _parse_two_sections(text: str) -> tuple[str, str]:
    # Basic split using headings; fall back to whole text if format is off.
    exact = ""
    short = ""
    m = re.split(r"\bShort Explanation:\s*", text, flags=re.IGNORECASE)
    if len(m) == 2:
        left = m[0]
        short = m[1].strip()
        left = re.split(r"\bExact Answer:\s*", left, flags=re.IGNORECASE)[-1]
        exact = left.strip()
    else:
        exact = text.strip()
        short = ""
    return exact, short


def generate_with_openai(prompt: str, model: str) -> str:
    _ensure_env_loaded()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise LLMError("OPENAI_API_KEY is not set.")

    url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/responses")
    timeout = float(os.getenv("OPENAI_TIMEOUT", "60"))
    max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
    backoff = float(os.getenv("OPENAI_RETRY_BACKOFF", "1.5"))
    payload = {
        "model": model,
        "input": prompt,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_err = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code >= 400:
                # Retry on 429 or 5xx
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = LLMError(f"OpenAI API error {resp.status_code}: {resp.text}")
                    if attempt < max_retries:
                        time.sleep(backoff * attempt)
                        continue
                raise LLMError(f"OpenAI API error {resp.status_code}: {resp.text}")
            data = resp.json()
            text = _extract_output_text(data)
            if not text:
                raise LLMError("OpenAI API returned no output text.")
            return text
        except requests.RequestException as e:
            last_err = LLMError(f"OpenAI request failed: {e}")
            if attempt < max_retries:
                time.sleep(backoff * attempt)
                continue
            raise last_err

    if last_err:
        raise last_err
    raise LLMError("OpenAI request failed unexpectedly.")


def generate_with_groq(prompt: str, model: str) -> str:
    _ensure_env_loaded()
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMError("GROQ_API_KEY is not set.")

    url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1/responses")
    timeout = float(os.getenv("GROQ_TIMEOUT", "60"))
    max_retries = int(os.getenv("GROQ_MAX_RETRIES", "3"))
    backoff = float(os.getenv("GROQ_RETRY_BACKOFF", "1.5"))
    payload = {
        "model": model,
        "input": prompt,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_err = None

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code >= 400:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = LLMError(f"Groq API error {resp.status_code}: {resp.text}")
                    if attempt < max_retries:
                        time.sleep(backoff * attempt)
                        continue
                raise LLMError(f"Groq API error {resp.status_code}: {resp.text}")
            data = resp.json()
            text = _extract_output_text(data)
            if not text:
                raise LLMError("Groq API returned no output text.")
            return text
        except requests.RequestException as e:
            last_err = LLMError(f"Groq request failed: {e}")
            if attempt < max_retries:
                time.sleep(backoff * attempt)
                continue
            raise last_err

    if last_err:
        raise last_err
    raise LLMError("Groq request failed unexpectedly.")


def generate_with_gemini(prompt: str, model: str) -> str:
    # Placeholder: implement once endpoint/model spec is confirmed.
    raise LLMError(
        "Gemini provider not configured. Set up a Gemini endpoint or ask to implement it."
    )


def generate_with_grok(prompt: str, model: str) -> str:
    # Placeholder: implement once endpoint/model spec is confirmed.
    raise LLMError("Grok provider not configured. Set up a Grok endpoint or ask to implement it.")


def generate_answer(
    provider: str,
    model: str,
    question_text: str,
    command_word: str,
    marks: int | None,
    ms_chunks: Iterable[str],
) -> tuple[str, str]:
    prompt = _build_prompt(question_text, command_word, marks, ms_chunks)

    provider = provider.lower()
    if provider == "openai":
        raw = generate_with_openai(prompt, model)
    elif provider == "groq":
        raw = generate_with_groq(prompt, model)
    elif provider == "gemini":
        raw = generate_with_gemini(prompt, model)
    elif provider == "grok":
        raw = generate_with_grok(prompt, model)
    else:
        raise LLMError(f"Unknown provider: {provider}")

    exact, short = _parse_two_sections(raw)
    return exact, short
