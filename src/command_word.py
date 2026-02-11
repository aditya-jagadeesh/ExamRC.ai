import re

COMMAND_WORDS = [
    "identify",
    "state",
    "give",
    "define",
    "describe",
    "explain",
    "outline",
    "compare",
    "contrast",
    "discuss",
    "evaluate",
    "justify",
]

COMMAND_ALIASES = {
    "how": "explain",
    "why": "explain",
    "what": "describe",
    "list": "identify",
    "name": "identify",
}


def _infer_from_patterns(lower: str) -> str | None:
    # Follow-up style requests: "give me 2 more points"
    if re.search(r"\bgive\s+me\s+\d+\s+more\s+points?\b", lower):
        return "give"
    if re.search(r"\bmore\s+points?\b", lower):
        return "give"

    # Common natural-language variants
    if re.search(r"\bhow\b", lower):
        return "explain"
    if re.search(r"\bwhy\b", lower):
        return "explain"
    if re.search(r"\bwhat\s+is\b|\bwhat\s+are\b", lower):
        return "describe"
    return None


def detect_command_word(question_text: str) -> str:
    lower = question_text.lower()
    for word in COMMAND_WORDS:
        if re.search(rf"\b{word}\b", lower):
            return word
    for alias, mapped in COMMAND_ALIASES.items():
        if re.search(rf"\b{alias}\b", lower):
            return mapped
    inferred = _infer_from_patterns(lower)
    if inferred:
        return inferred
    return "unspecified"


def detect_marks(question_text: str) -> int | None:
    # Common format: "(4)" or "(4 marks)"
    m = re.search(r"\((\d+)\s*(?:marks?)?\)", question_text.lower())
    if m:
        return int(m.group(1))

    # Alternative: "4 marks"
    m = re.search(r"\b(\d+)\s*marks?\b", question_text.lower())
    if m:
        return int(m.group(1))

    return None
