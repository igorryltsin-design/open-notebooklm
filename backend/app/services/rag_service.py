"""RAG layer: chunking, indexing in ChromaDB, retrieval."""

from __future__ import annotations

import logging
import math
import re
import uuid
from collections import Counter
from typing import Callable, Optional

import chromadb
from sentence_transformers import SentenceTransformer

from app.config import (
    CHROMA_HOST,
    CHROMA_PORT,
    CHUNK_MAX,
    CHUNK_MIN,
    CHUNK_OVERLAP_MAX,
    CHUNK_OVERLAP_MIN,
    EMBEDDING_MODEL,
    RETRIEVAL_TOP_K,
)

logger = logging.getLogger(__name__)

_embedder: Optional[SentenceTransformer] = None
_chroma_client: Optional[chromadb.HttpClient] = None
_lexical_cache: dict[str, dict] = {}

_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)
_PDF_PAGE_RE = re.compile(r"\[(?:OCR\s+)?PDF page\s+(\d+)\]", flags=re.IGNORECASE)
_OCR_PDF_PAGE_RE = re.compile(r"\[OCR\s+PDF page\s+\d+\]", flags=re.IGNORECASE)
_OCR_DOCX_IMAGE_RE = re.compile(r"\[OCR\s+DOCX image\s+\d+:", flags=re.IGNORECASE)
_PDF_TABLE_RE = re.compile(r"\[PDF table\s+(\d+)\]", flags=re.IGNORECASE)
_PDF_FIGURE_RE = re.compile(r"\[PDF figure\s+(\d+)\]", flags=re.IGNORECASE)
_PPTX_TABLE_RE = re.compile(r"\[PPTX table\s+(\d+)\]", flags=re.IGNORECASE)
_PPTX_FIGURE_RE = re.compile(r"\[PPTX figure\s+(\d+)\]", flags=re.IGNORECASE)
_SLIDE_RE = re.compile(r"^\s*Слайд\s+(\d+)\s*$", flags=re.IGNORECASE)
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_NUMBERED_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+){0,4}\s+[^\n]+$")
_ANCHOR_RE = re.compile(r"^\s*Якорь:\s*(.+?)\s*$", flags=re.IGNORECASE)
_CAPTION_RE = re.compile(r"^\s*Подпись:\s*(.+?)\s*$", flags=re.IGNORECASE)
_ANCHOR_PDF_PAGE_RE = re.compile(r"pdf:p(\d+)", flags=re.IGNORECASE)
_ANCHOR_PPTX_SLIDE_RE = re.compile(r"pptx:s(\d+)", flags=re.IGNORECASE)

VECTOR_CANDIDATE_MULTIPLIER = 4
LEXICAL_CANDIDATE_MULTIPLIER = 6
MIN_VECTOR_CANDIDATES = 12
MIN_LEXICAL_CANDIDATES = 20


def _get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        logger.info("Loading embedding model: %s", EMBEDDING_MODEL)
        try:
            _embedder = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
        except Exception as e:
            raise RuntimeError(
                f"Embedding model '{EMBEDDING_MODEL}' is not available locally. "
                "Rebuild backend image so model files are baked in."
            ) from e
    return _embedder


def _get_chroma() -> chromadb.HttpClient:
    global _chroma_client
    if _chroma_client is None:
        try:
            from chromadb.config import Settings
            _chroma_client = chromadb.HttpClient(
                host=CHROMA_HOST,
                port=CHROMA_PORT,
                settings=Settings(anonymized_telemetry=False),
            )
        except Exception:
            _chroma_client = chromadb.HttpClient(
                host=CHROMA_HOST,
                port=CHROMA_PORT,
            )
        logger.info("Connected to ChromaDB at %s:%s", CHROMA_HOST, CHROMA_PORT)
    return _chroma_client


def _invalidate_lexical_cache(document_id: str | None = None) -> None:
    if document_id is None:
        _lexical_cache.clear()
        return
    _lexical_cache.pop(str(document_id), None)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= 2]


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _source_kind_from_signals(
    *,
    source_type: str | None,
    anchor: str | None,
    section_path: str | None,
    text: str,
) -> str:
    st = str(source_type or "").strip().lower()
    anc = str(anchor or "").strip().lower()
    sec = str(section_path or "").strip().lower()
    src = str(text or "")
    if st.startswith("pdf") or st == "ocr_pdf" or anc.startswith("pdf:") or _PDF_PAGE_RE.search(src):
        return "pdf"
    if st.startswith("pptx") or anc.startswith("pptx:") or sec.startswith("слайд"):
        return "pptx"
    if st == "ocr_docx" or anc.startswith("docx:"):
        return "docx"
    return "text"


def _best_quote(text: str, *, fallback: str = "") -> str:
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", str(text or "")) if ln.strip()]
    for ln in lines[:24]:
        if _PDF_PAGE_RE.search(ln):
            continue
        if _PDF_TABLE_RE.search(ln) or _PDF_FIGURE_RE.search(ln) or _PPTX_TABLE_RE.search(ln) or _PPTX_FIGURE_RE.search(ln):
            continue
        if _ANCHOR_RE.match(ln) or _CAPTION_RE.match(ln):
            continue
        if len(ln) >= 18:
            return ln[:260]
    fb = str(fallback or "").strip()
    if fb:
        return fb[:260]
    return (lines[0][:260] if lines else "")


def _find_text_span(text: str, needle: str | None) -> tuple[int | None, int | None]:
    body = str(text or "")
    q = str(needle or "").strip()
    if not body or not q:
        return None, None
    low_idx = body.lower().find(q.lower())
    if low_idx >= 0:
        return low_idx, low_idx + len(q)
    compact_body = re.sub(r"\s+", " ", body).strip().lower()
    compact_q = re.sub(r"\s+", " ", q).strip().lower()
    if not compact_body or not compact_q:
        return None, None
    cidx = compact_body.find(compact_q)
    if cidx < 0:
        return None, None
    # Best-effort back-projection: find first word of compact needle in original body.
    first_word = compact_q.split(" ")[0].strip()
    if not first_word:
        return None, None
    widx = body.lower().find(first_word)
    if widx < 0:
        return None, None
    return widx, min(len(body), widx + len(q))


def build_source_locator(
    *,
    chunk_id: str,
    chunk_index: int | None,
    text: str,
    page: int | None,
    section_path: str | None,
    anchor: str | None,
    caption: str | None,
    source_type: str | None,
    highlight_hint: str | None = None,
) -> dict:
    kind = _source_kind_from_signals(
        source_type=source_type,
        anchor=anchor,
        section_path=section_path,
        text=text,
    )
    resolved_page = _safe_int(page)
    resolved_slide = None
    anc = str(anchor or "").strip()
    if resolved_page is None and anc:
        pm = _ANCHOR_PDF_PAGE_RE.search(anc)
        if pm:
            resolved_page = _safe_int(pm.group(1))
    if anc:
        sm = _ANCHOR_PPTX_SLIDE_RE.search(anc)
        if sm:
            resolved_slide = _safe_int(sm.group(1))
    if resolved_slide is None:
        sec = str(section_path or "").strip()
        if sec:
            sm = re.match(r"^\s*Слайд\s+(\d+)\s*$", sec, flags=re.IGNORECASE)
            if sm:
                resolved_slide = _safe_int(sm.group(1))
    quote = _best_quote(text, fallback=(str(caption or "").strip() or str(section_path or "").strip()))
    char_start, char_end = _find_text_span(text, highlight_hint or quote)
    locator = {
        "kind": kind,
        "chunk_id": str(chunk_id or "").strip(),
        "chunk_index": chunk_index,
        "page": resolved_page,
        "slide": resolved_slide,
        "section_path": (str(section_path or "").strip() or None),
        "anchor": (anc or None),
        "caption": (str(caption or "").strip() or None),
        "source_type": (str(source_type or "").strip() or None),
        "quote": quote or None,
        "char_start": char_start,
        "char_end": char_end,
    }
    return locator


def _infer_chunk_metadata(chunk_text: str) -> dict:
    meta: dict[str, object] = {}
    text = str(chunk_text or "")
    # Prefer the latest page marker in the chunk because overlaps may contain previous pages.
    page_matches = list(_PDF_PAGE_RE.finditer(text))
    if page_matches:
        try:
            meta["page"] = int(page_matches[-1].group(1))
        except (TypeError, ValueError):
            pass

    if _OCR_PDF_PAGE_RE.search(text):
        meta.setdefault("source_type", "ocr_pdf")
    elif _OCR_DOCX_IMAGE_RE.search(text):
        meta.setdefault("source_type", "ocr_docx")

    if _PDF_TABLE_RE.search(text):
        meta.setdefault("source_type", "pdf_table")
    elif _PDF_FIGURE_RE.search(text):
        meta.setdefault("source_type", "pdf_figure")
    elif _PPTX_TABLE_RE.search(text):
        meta.setdefault("source_type", "pptx_table")
    elif _PPTX_FIGURE_RE.search(text):
        meta.setdefault("source_type", "pptx_figure")

    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    if not lines:
        return meta

    for line in lines[:10]:
        if not meta.get("anchor"):
            anchor_m = _ANCHOR_RE.match(line)
            if anchor_m:
                anchor = anchor_m.group(1).strip()
                if anchor:
                    meta["anchor"] = anchor[:160]
                    continue
        if not meta.get("caption"):
            caption_m = _CAPTION_RE.match(line)
            if caption_m:
                caption = caption_m.group(1).strip()
                if caption:
                    meta["caption"] = caption[:220]
                    # don't continue: caption line can still be treated as section fallback if needed

    for line in lines[:6]:
        if _PDF_PAGE_RE.search(line):
            continue
        if _PDF_TABLE_RE.search(line) or _PDF_FIGURE_RE.search(line) or _PPTX_TABLE_RE.search(line) or _PPTX_FIGURE_RE.search(line):
            continue
        if _ANCHOR_RE.match(line) or _CAPTION_RE.match(line):
            continue
        slide_m = _SLIDE_RE.match(line)
        if slide_m:
            meta.setdefault("section_path", f"Слайд {slide_m.group(1)}")
            continue
        md_m = _MD_HEADING_RE.match(line)
        if md_m:
            heading = md_m.group(1).strip()
            if heading:
                meta.setdefault("section_path", heading[:180])
            continue
        if _NUMBERED_HEADING_RE.match(line):
            meta.setdefault("section_path", line[:180])
            continue
        # Fallback for short title-like first lines (avoid full sentences).
        word_count = len(line.split())
        if (
            2 <= word_count <= 12
            and len(line) <= 90
            and not re.search(r"[.!?…]$", line)
            and not line.startswith("[")
        ):
            meta.setdefault("section_path", line[:180])
        break

    return meta


def _row_from_meta(*, chunk_id: str, text: str, meta: dict | None = None) -> dict:
    m = meta or {}
    chunk_index = _safe_int(m.get("chunk_index"))
    page = _safe_int(m.get("page"))
    section_path = (str(m.get("section_path")).strip() if m.get("section_path") is not None else None) or None
    anchor = (str(m.get("anchor")).strip() if m.get("anchor") is not None else None) or None
    caption = (str(m.get("caption")).strip() if m.get("caption") is not None else None) or None
    source_type = (str(m.get("source_type")).strip() if m.get("source_type") is not None else None) or None
    return {
        "chunk_id": chunk_id,
        "text": text,
        "chunk_index": chunk_index,
        "page": page,
        "section_path": section_path,
        "anchor": anchor,
        "caption": caption,
        "source_type": source_type,
        "source_locator": build_source_locator(
            chunk_id=chunk_id,
            chunk_index=chunk_index,
            text=text,
            page=page,
            section_path=section_path,
            anchor=anchor,
            caption=caption,
            source_type=source_type,
        ),
        "meta": m,
    }


# ---------- Chunking -------------------------------------------------------

def chunk_text(text: str) -> list[str]:
    """Split *text* into chunks of 800-1200 chars with 150-250 char overlap."""
    target = (CHUNK_MIN + CHUNK_MAX) // 2  # 1000
    overlap = (CHUNK_OVERLAP_MIN + CHUNK_OVERLAP_MAX) // 2  # 200
    step = target - overlap  # 800

    sentences = re.split(r'(?<=[.!?])\s+', text)

    chunks: list[str] = []
    current = ""

    for sent in sentences:
        if len(current) + len(sent) + 1 > CHUNK_MAX and len(current) >= CHUNK_MIN:
            chunks.append(current.strip())
            # keep overlap
            words = current.split()
            overlap_text = ""
            for w in reversed(words):
                if len(overlap_text) + len(w) + 1 > overlap:
                    break
                overlap_text = w + " " + overlap_text
            current = overlap_text.strip() + " " + sent
        else:
            current = (current + " " + sent).strip() if current else sent

    if current.strip():
        chunks.append(current.strip())

    # Merge very small trailing chunk
    if len(chunks) > 1 and len(chunks[-1]) < CHUNK_MIN // 2:
        chunks[-2] = chunks[-2] + " " + chunks[-1]
        chunks.pop()

    return chunks


# ---------- Indexing -------------------------------------------------------

def _index_document_impl(
    document_id: str,
    chunks: list[str],
    *,
    batch_size: int = 32,
    progress_cb: Callable[[int, int], None] | None = None,
) -> int:
    """Common indexing implementation with optional per-batch progress callback."""
    embedder = _get_embedder()
    chroma = _get_chroma()

    collection = chroma.get_or_create_collection(
        name=f"doc_{document_id}",
        metadata={"hnsw:space": "cosine"},
    )

    total = len(chunks)
    done = 0
    for start in range(0, total, max(1, batch_size)):
        stop = min(start + batch_size, total)
        batch = chunks[start:stop]
        embeddings = embedder.encode(batch, show_progress_bar=False).tolist()
        ids = [f"{document_id}_{i}" for i in range(start, stop)]
        metadatas = []
        for i, doc in enumerate(batch, start=start):
            meta = {"chunk_index": i}
            inferred = _infer_chunk_metadata(doc)
            if inferred:
                meta.update(inferred)
            metadatas.append(meta)
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=batch,
            metadatas=metadatas,
        )
        done = stop
        if progress_cb is not None:
            progress_cb(done, total)

    logger.info("Indexed %d chunks for document %s", total, document_id)
    _invalidate_lexical_cache(document_id)
    return total


def index_document(document_id: str, chunks: list[str]) -> int:
    """Embed and store chunks in ChromaDB. Returns number of chunks stored."""
    return _index_document_impl(document_id, chunks)


def index_document_with_progress(
    document_id: str,
    chunks: list[str],
    progress_cb: Callable[[int, int], None] | None = None,
    *,
    batch_size: int = 32,
) -> int:
    """Sync indexing variant with progress callback (done, total)."""
    return _index_document_impl(
        document_id,
        chunks,
        batch_size=batch_size,
        progress_cb=progress_cb,
    )


def delete_document_index(document_id: str) -> None:
    """Remove ChromaDB collection for this document."""
    try:
        chroma = _get_chroma()
        chroma.delete_collection(name=f"doc_{document_id}")
        logger.info("Deleted index for document %s", document_id)
        _invalidate_lexical_cache(document_id)
    except Exception as e:
        logger.warning("Could not delete collection for document %s: %s", document_id, e)


def clear_all_indices() -> int:
    """Remove all Chroma collections. Returns number of removed collections (best effort)."""
    removed = 0
    chroma = _get_chroma()
    try:
        collections = chroma.list_collections()
    except Exception as e:
        logger.warning("Could not list Chroma collections: %s", e)
        return 0

    for col in collections or []:
        try:
            name = getattr(col, "name", None)
            if not name and isinstance(col, dict):
                name = col.get("name")
            if not name and isinstance(col, str):
                name = col
            if not name:
                continue
            chroma.delete_collection(name=name)
            removed += 1
        except Exception as e:
            logger.warning("Could not delete Chroma collection %s: %s", col, e)
    _invalidate_lexical_cache()
    return removed


# ---------- Retrieval ------------------------------------------------------

def _vector_retrieve(document_id: str, query: str, top_k: int) -> list[dict]:
    embedder = _get_embedder()
    chroma = _get_chroma()
    try:
        collection = chroma.get_collection(name=f"doc_{document_id}")
    except Exception:
        logger.warning("Collection for document %s not found", document_id)
        return []

    query_embedding = embedder.encode([query]).tolist()
    n_results = max(top_k, MIN_VECTOR_CANDIDATES)

    results = collection.query(
        query_embeddings=query_embedding,
        n_results=n_results,
        include=["documents", "distances", "metadatas"],
    )

    items: list[dict] = []
    if not results or not results.get("ids"):
        return items

    for cid, doc, dist, meta in zip(
        results["ids"][0],
        results["documents"][0],
        results["distances"][0],
        results.get("metadatas", [[]])[0] if results.get("metadatas") else [{} for _ in results["ids"][0]],
    ):
        base = _row_from_meta(chunk_id=str(cid), text=str(doc or ""), meta=(meta or {}))
        base["score_vector"] = max(0.0, min(1.0, round(1 - float(dist or 0.0), 6)))
        items.append(base)
    return items


def _build_lexical_index(document_id: str) -> dict:
    chroma = _get_chroma()
    collection = chroma.get_collection(name=f"doc_{document_id}")
    payload = collection.get(include=["documents", "metadatas"])

    ids = list(payload.get("ids") or [])
    docs = list(payload.get("documents") or [])
    metas = list(payload.get("metadatas") or [])
    size = min(len(ids), len(docs))
    rows: list[dict] = []
    df: Counter[str] = Counter()
    total_len = 0

    for idx in range(size):
        cid = ids[idx]
        text = str(docs[idx] or "")
        meta = metas[idx] if idx < len(metas) else {}
        tokens = _tokenize(text)
        if not text.strip():
            continue
        tf = Counter(tokens)
        uniq_terms = set(tf.keys())
        for term in uniq_terms:
            df[term] += 1
        doc_len = max(1, sum(tf.values()))
        total_len += doc_len
        rows.append({
            "chunk_id": cid,
            "text": text,
            "meta": meta or {},
            "tf": tf,
            "doc_len": doc_len,
        })

    avg_doc_len = (total_len / len(rows)) if rows else 1.0
    return {
        "rows": rows,
        "df": df,
        "doc_count": len(rows),
        "avg_doc_len": max(1.0, avg_doc_len),
    }


def _get_lexical_index(document_id: str) -> dict:
    key = str(document_id)
    cached = _lexical_cache.get(key)
    if cached is not None:
        return cached
    index = _build_lexical_index(document_id)
    _lexical_cache[key] = index
    return index


def _lexical_bm25_score(tf: Counter[str], doc_len: int, query_terms: list[str], df: Counter[str], doc_count: int, avg_doc_len: float) -> float:
    if not query_terms or doc_count <= 0:
        return 0.0
    k1 = 1.2
    b = 0.75
    score = 0.0
    for term in query_terms:
        freq = tf.get(term, 0)
        if freq <= 0:
            continue
        term_df = max(0, int(df.get(term, 0)))
        idf = math.log(1.0 + ((doc_count - term_df + 0.5) / (term_df + 0.5)))
        denom = freq + k1 * (1 - b + b * (doc_len / max(1.0, avg_doc_len)))
        score += idf * ((freq * (k1 + 1)) / max(1e-9, denom))
    return float(score)


def _lexical_retrieve(document_id: str, query: str, top_k: int) -> list[dict]:
    query_terms = _tokenize(query)
    if not query_terms:
        return []
    index = _get_lexical_index(document_id)
    rows = index.get("rows") or []
    if not rows:
        return []

    out: list[dict] = []
    seen_term_order: list[str] = []
    for t in query_terms:
        if t not in seen_term_order:
            seen_term_order.append(t)

    for row in rows:
        text = row["text"]
        tf = row["tf"]
        bm25 = _lexical_bm25_score(
            tf,
            int(row["doc_len"]),
            seen_term_order,
            index["df"],
            int(index["doc_count"]),
            float(index["avg_doc_len"]),
        )
        if bm25 <= 0:
            continue
        matched_terms = sum(1 for term in seen_term_order if tf.get(term, 0) > 0)
        coverage = matched_terms / max(1, len(seen_term_order))
        q_norm = re.sub(r"\s+", " ", query.strip()).lower()
        text_norm = re.sub(r"\s+", " ", text).lower()
        phrase_bonus = 0.4 if q_norm and len(q_norm) >= 8 and q_norm in text_norm else 0.0
        raw_score = bm25 + (coverage * 0.6) + phrase_bonus

        meta = row.get("meta") or {}
        base = _row_from_meta(chunk_id=str(row["chunk_id"]), text=text, meta=meta)
        base["score_lexical_raw"] = round(raw_score, 6)
        out.append(base)

    out.sort(key=lambda x: float(x.get("score_lexical_raw", 0.0) or 0.0), reverse=True)
    return out[:max(1, top_k)]


def _hybrid_rerank(query: str, vector_rows: list[dict], lexical_rows: list[dict], top_k: int) -> list[dict]:
    merged: dict[str, dict] = {}
    query_terms = []
    for t in _tokenize(query):
        if t not in query_terms:
            query_terms.append(t)
    query_norm = re.sub(r"\s+", " ", (query or "").strip()).lower()

    lexical_max = max([float(r.get("score_lexical_raw", 0.0) or 0.0) for r in lexical_rows] or [0.0])

    for row in vector_rows:
        cid = str(row.get("chunk_id", ""))
        if not cid:
            continue
        merged[cid] = dict(row)

    for row in lexical_rows:
        cid = str(row.get("chunk_id", ""))
        if not cid:
            continue
        cur = merged.get(cid)
        if cur is None:
            merged[cid] = dict(row)
        else:
            for key, value in row.items():
                if key not in cur or cur.get(key) in (None, "", 0):
                    cur[key] = value
                elif key == "meta" and isinstance(cur.get("meta"), dict) and isinstance(value, dict):
                    merged_meta = dict(value)
                    merged_meta.update(cur["meta"])
                    cur["meta"] = merged_meta

    ranked: list[dict] = []
    for row in merged.values():
        text = str(row.get("text", "") or "")
        text_norm = re.sub(r"\s+", " ", text).lower()
        vector_score = max(0.0, min(1.0, float(row.get("score_vector", 0.0) or 0.0)))
        lexical_raw = max(0.0, float(row.get("score_lexical_raw", 0.0) or 0.0))
        lexical_norm = min(1.0, (lexical_raw / lexical_max)) if lexical_max > 0 else 0.0
        coverage = 0.0
        if query_terms:
            matched = sum(1 for t in query_terms if t in text_norm)
            coverage = matched / max(1, len(query_terms))
        phrase_bonus = 0.12 if query_norm and len(query_norm) >= 8 and query_norm in text_norm else 0.0
        recency_marker_bonus = 0.03 if row.get("page") is not None or row.get("section_path") else 0.0
        structured_chunk_bonus = 0.03 if row.get("anchor") or row.get("source_type") else 0.0

        if vector_score > 0 and lexical_norm > 0:
            final = (vector_score * 0.60) + (lexical_norm * 0.25) + (coverage * 0.12) + phrase_bonus + recency_marker_bonus + structured_chunk_bonus
        elif vector_score > 0:
            final = (vector_score * 0.82) + (coverage * 0.10) + phrase_bonus + recency_marker_bonus + structured_chunk_bonus
        else:
            final = 0.12 + (lexical_norm * 0.62) + (coverage * 0.20) + phrase_bonus + recency_marker_bonus + structured_chunk_bonus

        ranked_row = dict(row)
        ranked_row["score"] = round(max(0.0, min(1.0, final)), 4)
        ranked.append(ranked_row)

    ranked.sort(key=lambda x: float(x.get("score", 0.0) or 0.0), reverse=True)

    # Best-effort de-duplication by normalized prefix.
    out: list[dict] = []
    seen_prefixes: set[str] = set()
    for row in ranked:
        text = str(row.get("text", "") or "")
        prefix = re.sub(r"\s+", " ", text).strip().lower()[:240]
        if prefix and prefix in seen_prefixes:
            continue
        if prefix:
            seen_prefixes.add(prefix)
        out.append(row)
        if len(out) >= max(1, top_k):
            break
    return out


def retrieve(document_id: str, query: str, top_k: int | None = None) -> list[dict]:
    """Return top-k relevant chunks for *query* from document collection.

    Each result dict: {"chunk_id": str, "text": str, "score": float}
    """
    k = top_k or RETRIEVAL_TOP_K
    vector_candidates = max(k * VECTOR_CANDIDATE_MULTIPLIER, MIN_VECTOR_CANDIDATES)
    lexical_candidates = max(k * LEXICAL_CANDIDATE_MULTIPLIER, MIN_LEXICAL_CANDIDATES)
    try:
        vector_rows = _vector_retrieve(document_id, query, top_k=vector_candidates)
    except Exception as e:
        logger.warning("Vector retrieval failed for document %s: %s", document_id, e)
        vector_rows = []

    try:
        lexical_rows = _lexical_retrieve(document_id, query, top_k=lexical_candidates)
    except Exception as e:
        logger.warning("Lexical retrieval failed for document %s: %s", document_id, e)
        lexical_rows = []

    if not vector_rows and not lexical_rows:
        return []

    try:
        ranked = _hybrid_rerank(query, vector_rows, lexical_rows, top_k=k)
    except Exception as e:
        logger.warning("Hybrid rerank failed for document %s: %s", document_id, e)
        ranked = []

    if ranked:
        return ranked

    # Fallback to vector-only rows in old format if hybrid stage fails unexpectedly.
    return [
        {
            "chunk_id": r.get("chunk_id"),
            "text": r.get("text", ""),
            "score": round(float(r.get("score_vector", 0.0) or 0.0), 4),
            "chunk_index": r.get("chunk_index"),
            "page": r.get("page"),
            "section_path": r.get("section_path"),
            "anchor": r.get("anchor"),
            "caption": r.get("caption"),
            "source_type": r.get("source_type"),
            "source_locator": r.get("source_locator") or build_source_locator(
                chunk_id=str(r.get("chunk_id", "")),
                chunk_index=r.get("chunk_index"),
                text=str(r.get("text", "") or ""),
                page=r.get("page"),
                section_path=r.get("section_path"),
                anchor=r.get("anchor"),
                caption=r.get("caption"),
                source_type=r.get("source_type"),
            ),
            "meta": r.get("meta") or {},
        }
        for r in vector_rows[:k]
    ]


def get_chunk(document_id: str, chunk_id: str) -> dict | None:
    """Return one indexed chunk by id with metadata/locator; None if not found."""
    cid = str(chunk_id or "").strip()
    if not cid:
        return None
    chroma = _get_chroma()
    try:
        collection = chroma.get_collection(name=f"doc_{document_id}")
    except Exception:
        logger.warning("Collection for document %s not found", document_id)
        return None
    try:
        payload = collection.get(ids=[cid], include=["documents", "metadatas"])
    except Exception as e:
        logger.warning("Chunk lookup failed for %s/%s: %s", document_id, cid, e)
        return None
    ids = list(payload.get("ids") or [])
    docs = list(payload.get("documents") or [])
    metas = list(payload.get("metadatas") or [])
    if not ids or not docs:
        return None
    resolved_id = str(ids[0] or cid)
    text = str(docs[0] or "")
    meta = metas[0] if metas else {}
    return _row_from_meta(chunk_id=resolved_id, text=text, meta=(meta or {}))
