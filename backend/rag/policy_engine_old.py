"""
rag/policy_engine.py — v3 with vector embeddings
──────────────────────────────────────────────────
Three-tier RAG:
  1. Dense embeddings (sentence-transformers all-MiniLM-L6-v2, free local)
  2. TF-IDF fallback (zero deps)
  3. Rule-based regex (always runs)

Install embeddings: pip install sentence-transformers
"""
import os, re, math, uuid, logging
from typing import List, Dict, Optional, Tuple

try:
    from logger_setup import get_logger
    _log = get_logger(__name__)
except ImportError:
    _log = logging.getLogger(__name__)

# ── Sentence-transformers (optional) ──────────────────────────────────────────
HAS_EMBEDDINGS = False
_embed_model   = None

def _try_load_embeddings():
    global HAS_EMBEDDINGS, _embed_model
    if HAS_EMBEDDINGS: return True
    try:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        HAS_EMBEDDINGS = True
        _log.info("Embeddings ready: all-MiniLM-L6-v2")
        return True
    except ImportError:
        _log.info("sentence-transformers not installed — using TF-IDF. "
                  "Upgrade: pip install sentence-transformers")
        return False
    except Exception as e:
        _log.warning(f"Could not load embedding model: {e}")
        return False

def _embed_texts(texts: List[str]) -> List[List[float]]:
    import numpy as np
    vecs = _embed_model.encode(texts, normalize_embeddings=True)
    return vecs.tolist()

def _cosine(a: List[float], b: List[float]) -> float:
    import numpy as np
    return float(np.dot(a, b))

# ── TF-IDF ─────────────────────────────────────────────────────────────────────
def _tok(text: str) -> List[str]:
    return re.findall(r"\b[a-z]{3,}\b", text.lower())

def _tfidf(text: str, idf: Dict) -> Dict[str, float]:
    tokens = _tok(text)
    tf     = {}
    for t in tokens: tf[t] = tf.get(t,0)+1
    n = max(len(tokens),1)
    return {t: (c/n)*idf.get(t,0) for t,c in tf.items()}

def _cosine_tfidf(a: Dict, b: Dict) -> float:
    keys = set(a) & set(b)
    dot  = sum(a[k]*b[k] for k in keys)
    na   = math.sqrt(sum(v**2 for v in a.values())) or 1
    nb   = math.sqrt(sum(v**2 for v in b.values())) or 1
    return dot/(na*nb)

# ── Compliance rules ────────────────────────────────────────────────────────────
RULES: List[Tuple[str, re.Pattern, bool, str]] = [
    ("Agent must introduce themselves",
     re.compile(r"\b(my name is|i('m| am)|this is)\b", re.I), True, "medium"),
    ("Agent must verify customer identity before account access",
     re.compile(r"\b(date of birth|account number|last.?four|verify|confirm your id)\b", re.I), True, "high"),
    ("Agent must offer a closing resolution check",
     re.compile(r"\b(anything else|anything more|anything i can|fully resolved)\b", re.I), True, "low"),
    ("Unauthorized large discount (>10% needs manager approval)",
     re.compile(r"\b(discount|credit).{0,20}[2-9][0-9]\s*%\b", re.I), False, "high"),
]


class PolicyEngine:
    def __init__(self, db=None, openrouter_client=None):
        self.db  = db
        self.llm = openrouter_client
        self._chunks_cache: List[Dict] = []
        self._idf: Dict[str, float]    = {}
        _try_load_embeddings()

    def index_document(self, title: str, content: str) -> str:
        chunks = self._chunk(content, title)
        method = "tfidf"

        if HAS_EMBEDDINGS:
            try:
                vecs = _embed_texts([c["text"] for c in chunks])
                for c, v in zip(chunks, vecs):
                    c["embedding"] = v
                method = "dense-embedding (all-MiniLM-L6-v2)"
            except Exception as e:
                _log.warning(f"Embedding failed, falling back to TF-IDF: {e}")

        if not HAS_EMBEDDINGS:
            self._rebuild_idf(chunks)

        doc_id = str(uuid.uuid4())
        for c in chunks: c["doc_id"] = doc_id

        if self.db:
            try: doc_id = self.db.insert_policy_doc(title, content, chunks)
            except Exception as e: _log.warning(f"DB insert_policy_doc: {e}")

        self._chunks_cache.extend(chunks)
        _log.info(f"Indexed '{title}': {len(chunks)} chunks ({method})")
        return doc_id

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict]:
        chunks = self._get_chunks()
        if not chunks: return []
        if HAS_EMBEDDINGS and chunks and "embedding" in chunks[0]:
            return self._dense_retrieve(query, chunks, top_k)
        return self._tfidf_retrieve(query, chunks, top_k)

    def check_compliance(self, transcript_text: str, call_id: str = "") -> List[Dict]:
        violations = list(self._rule_based_check(transcript_text))
        if self._get_chunks():
            violations.extend(self._semantic_check(transcript_text))
        if self.db and call_id:
            for v in violations:
                try:
                    self.db.insert_policy_violation(call_id, v.get("doc_id",""),
                                                    v["rule_text"], v["violation"], v["severity"])
                except Exception: pass
        return violations

    def _rule_based_check(self, text: str) -> List[Dict]:
        out = []
        for rule, pat, must_have, severity in RULES:
            matched = bool(pat.search(text))
            if must_have and not matched:
                out.append({"rule_text":rule,"violation":"Required behaviour not found","severity":severity,"method":"rule"})
            elif not must_have and matched:
                out.append({"rule_text":rule,"violation":"Potentially unauthorized action detected","severity":severity,"method":"rule"})
        return out

    def _dense_retrieve(self, query: str, chunks: List[Dict], top_k: int) -> List[Dict]:
        try:
            q_vec  = _embed_texts([query])[0]
            scored = [(c, _cosine(q_vec, c["embedding"])) for c in chunks if "embedding" in c]
            scored.sort(key=lambda x: x[1], reverse=True)
            return [{"text":c["text"],"title":c.get("title",""),"score":round(s,3)}
                    for c,s in scored[:top_k] if s > 0.15]
        except Exception as e:
            _log.warning(f"Dense retrieve failed: {e}")
            return self._tfidf_retrieve(query, chunks, top_k)

    def _tfidf_retrieve(self, query: str, chunks: List[Dict], top_k: int) -> List[Dict]:
        if not self._idf: self._rebuild_idf(chunks)
        q_vec  = _tfidf(query, self._idf)
        scored = [(c, _cosine_tfidf(q_vec, _tfidf(c["text"], self._idf))) for c in chunks]
        scored = [(c,s) for c,s in scored if s > 0.05]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [{"text":c["text"],"title":c.get("title",""),"score":round(s,3)}
                for c,s in scored[:top_k]]

    def _semantic_check(self, transcript: str) -> List[Dict]:
        results = self.retrieve(transcript, top_k=5)
        out = []
        for r in results:
            if r["score"] < 0.12:
                out.append({"rule_text":r["title"],
                            "violation":f"Transcript may not address: {r['text'][:80]}",
                            "severity":"low","method":"semantic"})
        return out

    def _get_chunks(self) -> List[Dict]:
        if self._chunks_cache: return self._chunks_cache
        if self.db:
            try: self._chunks_cache = self.db.get_policy_chunks() or []
            except Exception: pass
        return self._chunks_cache

    def _rebuild_idf(self, chunks: List[Dict]):
        N  = max(len(chunks), 1)
        df: Dict[str,int] = {}
        for c in chunks:
            for t in set(_tok(c["text"])): df[t] = df.get(t,0)+1
        self._idf = {t: math.log(N/d) for t,d in df.items()}

    @staticmethod
    def _chunk(text: str, title: str, size: int=400, overlap: int=50) -> List[Dict]:
        words  = text.split()
        chunks = []
        start  = 0
        idx    = 0
        while start < len(words):
            end = min(start+size, len(words))
            chunks.append({"chunk_idx":idx,"text":" ".join(words[start:end]),"title":title})
            if end == len(words): break
            start += size - overlap
            idx   += 1
        return chunks
