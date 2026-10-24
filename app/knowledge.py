from typing import Any

from app.vector_store import search_knowledge

NO_ANSWER = (
    "I couldn't find a verified answer in the telecom knowledge base. "
    "Please contact customer support for help with this question."
)


def format_knowledge_result(question: str) -> tuple[str, str | None]:
    matches = search_knowledge(question, limit=1)
    if not matches:
        return NO_ANSWER, None

    match = matches[0]
    metadata: dict[str, Any] = match["metadata"]
    answer = metadata.get("answer") or match["text"]
    source = metadata.get("source", "knowledge base")
    page = metadata.get("page")
    citation = f"{source}, page {page}" if page else source
    return answer, citation


def format_voice_knowledge_result(question: str) -> str:
    matches = search_knowledge(question, limit=3)
    if not matches:
        return NO_ANSWER

    excerpts = []
    for match in matches:
        metadata = match["metadata"]
        source = metadata.get("source", "knowledge base")
        page = metadata.get("page")
        citation = f"{source}, page {page}" if page else source
        excerpts.append(f"Source: {citation}\n{match['text']}")
    return "\n\n".join(excerpts)
