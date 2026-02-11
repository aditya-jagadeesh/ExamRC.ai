from pathlib import Path
from io import BytesIO
import pdfplumber


def load_pdf_text(pdf_path: Path) -> str:
    """Extract text from a PDF into a single string."""
    texts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text:
                texts.append(page_text)
    return "\n\n".join(texts)


def load_pdf_text_from_bytes(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes into a single string."""
    texts = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            if page_text:
                texts.append(page_text)
    return "\n\n".join(texts)
