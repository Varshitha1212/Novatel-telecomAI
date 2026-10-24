import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

from dotenv import load_dotenv
from pypdf import PdfReader
from qdrant_client import QdrantClient, models

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_BASE_PATH = PROJECT_ROOT / "knowledge_base"
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "telecom_support")
EMBEDDING_MODEL = "BAAI/bge-small-en"
MIN_COSINE_SCORE = 0.78
CHUNK_SIZE = 900
CHUNK_OVERLAP = 150

_client: QdrantClient | None = None
_query_lock = Lock()



def _get_client() -> QdrantClient:
    global _client

    if _client is None:
        url = os.getenv("QDRANT_URL", "").strip().strip("\"'")
        api_key = os.getenv("QDRANT_API_KEY", "").strip().strip("\"'")

        if not url:
            raise ValueError("QDRANT_URL is missing from environment variables.")

        if "\r" in url or "\n" in url:
            raise ValueError("QDRANT_URL contains invalid newline characters.")

        if "\r" in api_key or "\n" in api_key:
            raise ValueError(
                "QDRANT_API_KEY contains newline characters. "
                "Update it in Render Environment settings."
            )

        _client = QdrantClient(
            url=url,
            api_key=api_key or None,
        )

    return _client


def _ensure_collection(client: QdrantClient) -> None:
    if client.collection_exists(COLLECTION_NAME):
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=models.VectorParams(
            size=client.get_embedding_size(EMBEDDING_MODEL),
            distance=models.Distance.COSINE,
        ),
    )


def _chunk_text(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    chunks = []
    start = 0
    while start < len(normalized):
        end = min(start + CHUNK_SIZE, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start + CHUNK_SIZE // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(normalized[start:end].strip())
        if end == len(normalized):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _metadata(**values: str | int) -> dict[str, str | int]:
    return values


def _load_faq_documents(path: Path) -> list[tuple[str, dict[str, str | int]]]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{path} must contain a JSON list of FAQ entries.")

    documents = []
    for index, faq in enumerate(records):
        if not isinstance(faq, dict) or not faq.get("question") or not faq.get("answer"):
            raise ValueError(f"FAQ entry {index + 1} in {path} needs a question and answer.")
        category = str(faq.get("category", "General"))
        question = str(faq["question"])
        answer = str(faq["answer"])
        keywords = str(faq.get("keywords", ""))
        text = (
            f"Category: {category}\nQuestion: {question}\n"
            f"Keywords: {keywords}\nAnswer: {answer}"
        )
        documents.append(
            (
                text,
                _metadata(
                    source=path.name,
                    category=category,
                    question=question,
                    answer=answer,
                ),
            )
        )
    return documents


def _load_file_documents(path: Path) -> list[tuple[str, dict[str, str | int]]]:
    if path.suffix.lower() == ".pdf":
        pages = [
            (page.extract_text() or "", page_number)
            for page_number, page in enumerate(PdfReader(str(path)).pages, start=1)
        ]
    else:
        pages = [(path.read_text(encoding="utf-8"), 0)]

    documents = []
    relative_source = str(path.relative_to(KNOWLEDGE_BASE_PATH))
    filename = path.stem.casefold()
    document_category = (
        "Plans"
        if any(term in filename for term in ("plan", "tariff", "pricing"))
        else "Network"
        if any(term in filename for term in ("network", "coverage"))
        else "Billing"
        if any(term in filename for term in ("billing", "bill", "payment"))
        else None
    )
    for page_text, page_number in pages:
        for chunk in _chunk_text(page_text):
            metadata: dict[str, str | int] = {"source": relative_source}
            if document_category:
                metadata["category"] = document_category
            if page_number:
                metadata["page"] = page_number
            documents.append((chunk, metadata))
    return documents


def _all_source_documents() -> list[tuple[str, dict[str, str | int]]]:
    if not KNOWLEDGE_BASE_PATH.is_dir():
        raise FileNotFoundError(f"Knowledge base folder not found: {KNOWLEDGE_BASE_PATH}")

    sources = sorted(
        path
        for path in KNOWLEDGE_BASE_PATH.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".pdf", ".md", ".txt"}
    )
    if not sources:
        raise FileNotFoundError(
            f"No JSON, PDF, Markdown, or text files found in {KNOWLEDGE_BASE_PATH}"
        )

    documents = []
    for path in sources:
        if path.suffix.lower() == ".json":
            documents.extend(_load_faq_documents(path))
        else:
            documents.extend(_load_file_documents(path))
    return documents


def _delete_collection_points(client: QdrantClient) -> None:
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=256,
            offset=offset,
            with_payload=False,
            with_vectors=False,
        )
        if points:
            client.delete(
                collection_name=COLLECTION_NAME,
                points_selector=models.PointIdsList(points=[point.id for point in points]),
                wait=True,
            )
        if offset is None:
            break


def ingest_knowledge_base() -> int:
    documents = _all_source_documents()
    client = _get_client()
    _ensure_collection(client)
    _delete_collection_points(client)

    if documents:
        source_ids = [
            str(metadata["source"])
            for _, metadata in documents
        ]
        ids = [
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"telecom-support:{source}:{index}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}",
                )
            )
            for index, ((text, _), source) in enumerate(zip(documents, source_ids))
        ]
        client.upload_collection(
            collection_name=COLLECTION_NAME,
            vectors=[
                models.Document(text=text, model=EMBEDDING_MODEL)
                for text, _ in documents
            ],
            payload=[
                {"document": text, **metadata}
                for text, metadata in documents
            ],
            ids=ids,
            batch_size=64,
            wait=True,
        )
    return len(documents)


def ensure_knowledge_base_ingested() -> int:
    client = _get_client()
    _ensure_collection(client)
    count = client.count(collection_name=COLLECTION_NAME, exact=True).count
    if count == 0:
        return ingest_knowledge_base()
    return count


def search_knowledge(
    question: str,
    limit: int = 3,
    categories: set[str] | None = None,
) -> list[dict[str, Any]]:
    query = question.strip()
    if not query:
        return []

    client = _get_client()
    _ensure_collection(client)
    with _query_lock:
        results = client.query_points(
            collection_name=COLLECTION_NAME,
            query=models.Document(text=query, model=EMBEDDING_MODEL),
            limit=limit,
            query_filter=(
                models.Filter(
                    must=[
                        models.FieldCondition(
                            key="category",
                            match=models.MatchAny(any=list(categories)),
                        )
                    ]
                )
                if categories
                else None
            ),
            with_payload=True,
            score_threshold=MIN_COSINE_SCORE,
        )

    matches = []
    for point in results.points:
        payload = dict(point.payload or {})
        text = payload.pop("document", None)
        if text is not None:
            matches.append(
                {
                    "text": text,
                    "metadata": payload,
                    "score": point.score,
                }
            )
    return matches
