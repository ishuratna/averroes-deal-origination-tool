"""
Document Q&A — a real vector-RAG pipeline, written to be READ as much as run.

THE WHOLE IDEA IN FOUR STEPS
  1. INGEST    a document -> extract plain text (locally, zero AI cost)
  2. CHUNK     the text into overlapping ~1,200-char pieces (a chunk is the
               unit of retrieval: small enough to be specific, big enough to
               carry meaning; overlap stops facts being cut in half)
  3. EMBED     every chunk with an embedding model -> a 768-number vector
               where texts with similar MEANING are numerically close.
               Store text + vector in BigQuery (our "vector store").
  4. ASK       embed the QUESTION with the same model, find the K chunks
               whose vectors are closest (cosine similarity), hand exactly
               those chunks to Gemini with a strict "answer only from these,
               cite doc and page" contract.

That last step is Retrieval-Augmented Generation: the model never sees the
whole library, only the few passages most relevant to this question.

Design notes (the interview answers):
  * Brute-force cosine in numpy, not a vector database. At our scale
    (thousands of chunks, 768 dims) exact search takes milliseconds; a vector
    DB earns its keep at millions of chunks. Simplest thing that is correct.
  * Embeddings are cached in process memory and reloaded only when the
    library changes - BigQuery is the source of truth (doctrine), memory is
    just a view of it.
  * Chunking is page-aware so citations can say "IM.pdf, page 12".
  * The answer call is UNGROUNDED (no web search): the retrieved chunks ARE
    the grounding. Costs pennies; logged, not budget-capped like grounded.
"""
import io
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
from google.cloud import bigquery

logger = logging.getLogger(__name__)

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-004")   # 768 dims
CHUNK_CHARS = 1200        # ~300 tokens: one coherent passage
CHUNK_OVERLAP = 200       # so a sentence on a boundary appears in both chunks
TOP_K = 8                 # chunks handed to the model per question
EMBED_BATCH = 100         # API max per embed call

_cache_lock = threading.Lock()
_cache: Dict = {"etag": None, "matrix": None, "rows": []}   # in-memory vector index


# ── Tables ────────────────────────────────────────────────────────────────────

def _docs_id(bq) -> str:
    return f"{bq.project_id}.{bq.dataset_id}.doc_library"


def _chunks_id(bq) -> str:
    return f"{bq.project_id}.{bq.dataset_id}.doc_chunks"


def ensure_tables(bq):
    if not bq.client:
        return
    try:
        bq.client.get_table(_docs_id(bq))
    except Exception:
        bq.client.create_table(bigquery.Table(_docs_id(bq), schema=[
            bigquery.SchemaField("doc_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("doc_name", "STRING"),
            bigquery.SchemaField("company_name", "STRING"),
            bigquery.SchemaField("pages", "INT64"),
            bigquery.SchemaField("chunks", "INT64"),
            bigquery.SchemaField("gcs_path", "STRING"),
            bigquery.SchemaField("uploaded_at", "TIMESTAMP"),
        ]))
        logger.info("Created doc_library table")
    try:
        bq.client.get_table(_chunks_id(bq))
    except Exception:
        bq.client.create_table(bigquery.Table(_chunks_id(bq), schema=[
            bigquery.SchemaField("doc_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("doc_name", "STRING"),
            bigquery.SchemaField("company_name", "STRING"),
            bigquery.SchemaField("chunk_index", "INT64"),
            bigquery.SchemaField("page", "INT64"),
            bigquery.SchemaField("text", "STRING"),
            # THE VECTOR: 768 floats per chunk. BigQuery stores it as a
            # repeated FLOAT64 column - our vector store is just a table.
            bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),
            bigquery.SchemaField("created_at", "TIMESTAMP"),
        ]))
        logger.info("Created doc_chunks table")


# ── Step 1: text extraction (local, deterministic, free) ────────────────────

def extract_text(content: bytes, filename: str) -> List[Tuple[int, str]]:
    """Returns [(page_number, page_text), ...]. txt/md count as one page."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return [(i + 1, (p.extract_text() or "")) for i, p in enumerate(reader.pages)]
    if lower.endswith(".docx"):
        import docx
        d = docx.Document(io.BytesIO(content))
        text = "\n".join(p.text for p in d.paragraphs)
        return [(1, text)]
    if lower.endswith((".txt", ".md")):
        return [(1, content.decode("utf-8", errors="replace"))]
    raise ValueError("Unsupported file type. Use PDF, DOCX, TXT or MD.")


# ── Step 2: chunking ─────────────────────────────────────────────────────────

def chunk_pages(pages: List[Tuple[int, str]]) -> List[Dict]:
    """Overlapping fixed-size chunks, breaking at sentence ends where possible,
    each remembering the page it started on (for citations)."""
    chunks: List[Dict] = []
    for page_no, text in pages:
        text = re.sub(r"[ \t]+", " ", text or "").strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(start + CHUNK_CHARS, len(text))
            if end < len(text):  # prefer to break at a sentence boundary
                dot = text.rfind(". ", start + CHUNK_CHARS // 2, end)
                if dot > 0:
                    end = dot + 1
            piece = text[start:end].strip()
            if len(piece) > 50:  # skip crumbs
                chunks.append({"page": page_no, "text": piece})
            if end >= len(text):
                break
            start = end - CHUNK_OVERLAP  # the overlap
    for i, c in enumerate(chunks):
        c["chunk_index"] = i
    return chunks


# ── Step 3: embeddings ───────────────────────────────────────────────────────

def embed_texts(texts: List[str]) -> List[List[float]]:
    """Text -> 768-dim vector. Same model MUST embed both chunks and
    questions, or the geometry means nothing."""
    from google import genai
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    out: List[List[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i:i + EMBED_BATCH]
        resp = client.models.embed_content(model=EMBED_MODEL, contents=batch)
        out.extend([list(e.values) for e in resp.embeddings])
    return out


# ── Ingest: the whole write path ─────────────────────────────────────────────

def ingest_document(bq, content: bytes, filename: str, company_name: str = "",
                    gcs_path: str = "") -> Dict:
    ensure_tables(bq)
    pages = extract_text(content, filename)
    chunks = chunk_pages(pages)
    if not chunks:
        return {"status": "Error", "detail": "No readable text found in the document."}

    vectors = embed_texts([c["text"] for c in chunks])

    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    # Batched DML insert; the embedding goes in as an ARRAY parameter.
    BATCH = 50
    for b in range(0, len(chunks), BATCH):
        params, values = [], []
        for j, (c, v) in enumerate(zip(chunks[b:b + BATCH], vectors[b:b + BATCH])):
            values.append(f"(@d{j}_id, @d{j}_name, @d{j}_co, @d{j}_idx, @d{j}_pg, @d{j}_tx, @d{j}_emb, TIMESTAMP('{now}'))")
            params += [
                bigquery.ScalarQueryParameter(f"d{j}_id", "STRING", doc_id),
                bigquery.ScalarQueryParameter(f"d{j}_name", "STRING", filename),
                bigquery.ScalarQueryParameter(f"d{j}_co", "STRING", company_name or ""),
                bigquery.ScalarQueryParameter(f"d{j}_idx", "INT64", c["chunk_index"]),
                bigquery.ScalarQueryParameter(f"d{j}_pg", "INT64", c["page"]),
                bigquery.ScalarQueryParameter(f"d{j}_tx", "STRING", c["text"]),
                bigquery.ArrayQueryParameter(f"d{j}_emb", "FLOAT64", v),
            ]
        bq.client.query(
            f"""INSERT INTO `{_chunks_id(bq)}`
                (doc_id, doc_name, company_name, chunk_index, page, text, embedding, created_at)
                VALUES {', '.join(values)}""",
            job_config=bigquery.QueryJobConfig(query_parameters=params)).result()

    bq.client.query(
        f"""INSERT INTO `{_docs_id(bq)}`
            (doc_id, doc_name, company_name, pages, chunks, gcs_path, uploaded_at)
            VALUES (@id, @name, @co, @pg, @ch, @gcs, TIMESTAMP('{now}'))""",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", doc_id),
            bigquery.ScalarQueryParameter("name", "STRING", filename),
            bigquery.ScalarQueryParameter("co", "STRING", company_name or ""),
            bigquery.ScalarQueryParameter("pg", "INT64", len(pages)),
            bigquery.ScalarQueryParameter("ch", "INT64", len(chunks)),
            bigquery.ScalarQueryParameter("gcs", "STRING", gcs_path or ""),
        ])).result()

    _invalidate_cache()
    logger.info(f"[DocRAG] Ingested {filename}: {len(pages)} pages -> {len(chunks)} chunks")
    return {"status": "Success", "doc_id": doc_id, "doc_name": filename,
            "pages": len(pages), "chunks": len(chunks)}


# ── Step 4a: retrieval (the R in RAG) ───────────────────────────────────────

def _invalidate_cache():
    with _cache_lock:
        _cache["etag"] = None


def _load_index(bq):
    """Pull every chunk's vector into one numpy matrix (rows normalised so a
    dot product IS cosine similarity). Cached until the library changes."""
    rows = list(bq.client.query(
        f"""SELECT doc_id, doc_name, company_name, chunk_index, page, text, embedding
            FROM `{_chunks_id(bq)}`""").result())
    if not rows:
        return None, []
    matrix = np.array([list(r.embedding) for r in rows], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms, rows


def retrieve(bq, question: str, doc_id: str = "", company_name: str = "",
             top_k: int = TOP_K) -> List[Dict]:
    ensure_tables(bq)
    with _cache_lock:
        etag = f"{_chunks_id(bq)}"  # etag by row count, cheap staleness check
        count = list(bq.client.query(
            f"SELECT COUNT(*) AS n FROM `{_chunks_id(bq)}`").result())[0].n
        etag = f"{etag}:{count}"
        if _cache["etag"] != etag:
            _cache["matrix"], _cache["rows"] = _load_index(bq)
            _cache["etag"] = etag
        matrix, rows = _cache["matrix"], _cache["rows"]
    if matrix is None:
        return []

    q_vec = np.array(embed_texts([question])[0], dtype=np.float32)
    q_vec = q_vec / (np.linalg.norm(q_vec) or 1.0)

    # THE HEART OF IT: one matrix multiply = cosine similarity of the
    # question against every chunk in the library.
    scores = matrix @ q_vec

    order = np.argsort(-scores)
    out = []
    for i in order:
        r = rows[int(i)]
        if doc_id and r.doc_id != doc_id:
            continue
        if company_name and (r.company_name or "").lower() != company_name.lower():
            continue
        out.append({"doc_id": r.doc_id, "doc_name": r.doc_name,
                    "company_name": r.company_name, "page": int(r.page or 0),
                    "chunk_index": int(r.chunk_index or 0),
                    "text": r.text, "score": round(float(scores[int(i)]), 4)})
        if len(out) >= top_k:
            break
    return out


# ── Step 4b: generation with a strict citation contract ─────────────────────

def ask(bq, question: str, doc_id: str = "", company_name: str = "") -> Dict:
    passages = retrieve(bq, question, doc_id=doc_id, company_name=company_name)
    if not passages:
        return {"status": "Success", "answer": "No documents in the library match this scope yet. Upload a document first.",
                "sources": []}

    context = "\n\n".join(
        f"[{i+1}] From \"{p['doc_name']}\", page {p['page']}:\n{p['text']}"
        for i, p in enumerate(passages))

    prompt = f"""You answer questions using ONLY the numbered passages below, retrieved from
the user's own document library. Rules, strictly enforced:
1. Every factual claim must cite its passage like [1] or [2][3].
2. If the passages do not contain the answer, say exactly that. Never use
   outside knowledge, never guess, never extrapolate beyond the text.
3. Quote figures exactly as written in the passages.
4. Be concise: a short direct answer, then supporting detail if useful.

PASSAGES:
{context}

QUESTION: {question}

ANSWER:"""

    from google import genai
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    answer = (resp.text or "").strip()

    return {"status": "Success", "answer": answer,
            "sources": [{"n": i + 1, "doc_name": p["doc_name"], "page": p["page"],
                         "company_name": p["company_name"], "score": p["score"],
                         "snippet": p["text"][:400]} for i, p in enumerate(passages)]}


# ── Library management ───────────────────────────────────────────────────────

def list_documents(bq) -> List[Dict]:
    ensure_tables(bq)
    return [dict(r) for r in bq.client.query(
        f"""SELECT doc_id, doc_name, company_name, pages, chunks,
                   FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', uploaded_at) AS uploaded_at
            FROM `{_docs_id(bq)}` ORDER BY uploaded_at DESC""").result()]


def delete_document(bq, doc_id: str) -> int:
    for table in (_chunks_id(bq), _docs_id(bq)):
        bq.client.query(f"DELETE FROM `{table}` WHERE doc_id = @id",
                        job_config=bigquery.QueryJobConfig(query_parameters=[
                            bigquery.ScalarQueryParameter("id", "STRING", doc_id),
                        ])).result()
    _invalidate_cache()
    return 1
