from app.vector_store import KNOWLEDGE_BASE_PATH, ingest_knowledge_base


if __name__ == "__main__":
    document_count = ingest_knowledge_base()
    print(f"Ingested {document_count} document chunks into the Qdrant vector database.")
    print(f"Sources: {KNOWLEDGE_BASE_PATH}")
