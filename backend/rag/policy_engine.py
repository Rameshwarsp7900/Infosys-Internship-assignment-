"""
rag/policy_engine.py — v3 with vector embeddings
──────────────────────────────────────────────────
PRIMARY:  sentence-transformers (all-MiniLM-L6-v2, 384-dim, free, local)
FALLBACK: TF-IDF cosine similarity
"""
import os,re,math,json,logging,warnings
from typing import List,Dict,Optional,Tuple
log=logging.getLogger(__name__)
# Suppress FutureWarning from transformers tokenizer (cosmetic, not a bug)
warnings.filterwarnings("ignore", category=FutureWarning, module="transformers")

_encoder=None
_USE_EMB=None

def _get_encoder():
    global _encoder,_USE_EMB
    if _USE_EMB is not None: return _encoder
    try:
        from sentence_transformers import SentenceTransformer
        m=os.getenv("EMBEDDING_MODEL","all-MiniLM-L6-v2")
        log.info(f"Loading embedding model: {m}")
        _encoder=SentenceTransformer(m)
        _USE_EMB=True
        log.info(f"Embeddings ready ({_encoder.get_sentence_embedding_dimension()}d)")
    except ImportError:
        log.warning("sentence-transformers not installed → TF-IDF mode. pip install sentence-transformers")
        _USE_EMB=False
    except Exception as e:
        log.warning(f"Embedding model failed ({e}) → TF-IDF mode")
        _USE_EMB=False
    return _encoder

def encode(texts:List[str])->Optional[List[List[float]]]:
    enc=_get_encoder()
    if not enc: return None
    try:
        vecs=enc.encode(texts,normalize_embeddings=True)
        return [v.tolist() for v in vecs]
    except Exception as e:
        log.error(f"Encoding failed: {e}"); return None

def cosine(a,b):
    try: return sum(x*y for x,y in zip(a,b))
    except: return 0.0

def _split_chunks(text:str,size=250,overlap=40)->List[str]:
    words=text.split(); chunks=[]; i=0
    while i<len(words):
        chunks.append(" ".join(words[i:i+size])); i+=size-overlap
    return chunks

def _tfidf(docs:List[str],query:str,top_k:int=5)->List[Tuple[int,float]]:
    all_docs=docs+[query]; vocab={}
    for doc in all_docs:
        for w in re.findall(r"\b\w+\b",doc.lower()):
            if w not in vocab: vocab[w]=len(vocab)
    N=len(all_docs); df=[0]*len(vocab)
    for doc in all_docs:
        seen=set()
        for w in re.findall(r"\b\w+\b",doc.lower()):
            if w in vocab and w not in seen: df[vocab[w]]+=1; seen.add(w)
    def vec(doc):
        words=re.findall(r"\b\w+\b",doc.lower()); tf=[0.0]*len(vocab)
        for w in words:
            if w in vocab: tf[vocab[w]]+=1
        n=len(words) or 1
        v=[(tf[i]/n)*(math.log(N/(df[i]+1))+1) for i in range(len(vocab))]
        norm=math.sqrt(sum(x*x for x in v)) or 1
        return [x/norm for x in v]
    qv=vec(query)
    scores=sorted([(i,sum(a*b for a,b in zip(qv,vec(d)))) for i,d in enumerate(docs)],key=lambda x:x[1],reverse=True)
    return scores[:top_k]

class PolicyEngine:
    RULES=[
        ("Agent must introduce themselves at call start",r"\b(my name is|this is|i am|i'm)\b",True,"medium"),
        ("Agent must verify customer identity",r"\b(date of birth|account number|last (4|four)|verify|confirm your (name|identity))\b",True,"high"),
        ("Agent must offer closing summary",r"\b(anything else|anything more|help you with today)\b",True,"low"),
        ("No confrontational language",r"\b(your fault|you should have|not my problem|deal with it)\b",False,"high"),
        ("No unauthorized promises",r"\b(i (will|can) (give|offer) you (\d+)%|free (month|service))\b",False,"high"),
    ]

    def __init__(self,db=None,openrouter_client=None):
        self.db=db; self.llm=openrouter_client
        # _chunks_cache is a property alias so tests can set it directly
        self._cache:List[Dict]=[]
        self._load_chunks()

    @property
    def _chunks_cache(self):
        return self._cache

    @_chunks_cache.setter
    def _chunks_cache(self, value):
        self._cache = value

    def index_document(self,title:str,content:str)->str:
        raws=_split_chunks(content)
        chunk_dicts=[{"text":c} for c in raws]
        embeddings=encode(raws)
        mode="embedding" if embeddings else "tfidf"
        if embeddings:
            for i,emb in enumerate(embeddings): chunk_dicts[i]["embedding"]=emb
        log.info(f"Indexed '{title}': {len(raws)} chunks ({mode})")
        doc_id=""
        if self.db:
            try: doc_id=self.db.insert_policy_doc(title,content,chunk_dicts)
            except Exception as e: log.error(f"persist policy: {e}")
        for i,ch in enumerate(chunk_dicts):
            self._cache.append({"doc_id":doc_id or f"local_{title}","title":title,
                                 "text":ch["text"],"chunk_idx":i,"embedding":ch.get("embedding")})
        return doc_id

    def _load_chunks(self):
        if not self.db: return
        try:
            for ch in self.db.get_policy_chunks():
                emb=ch.get("embedding")
                if isinstance(emb,str):
                    try: emb=json.loads(emb)
                    except: emb=None
                self._cache.append({"doc_id":ch.get("doc_id",""),"title":ch.get("title",""),
                                     "text":ch.get("text",""),"chunk_idx":ch.get("chunk_idx",0),"embedding":emb})
            if self._cache: log.info(f"Loaded {len(self._cache)} policy chunks")
        except Exception as e: log.debug(f"load_chunks: {e}")

    def retrieve(self,query:str,top_k:int=5)->List[Dict]:
        chunks=self._cache
        if not chunks: return []
        qemb=encode([query])
        if qemb and any(c.get("embedding") for c in chunks):
            qv=qemb[0]
            scored=sorted([(cosine(qv,c["embedding"]),c) for c in chunks if c.get("embedding")],key=lambda x:x[0],reverse=True)
            return [{"text":c["text"],"title":c["title"],"doc_id":c["doc_id"],"score":round(s,4)} for s,c in scored[:top_k] if s>0.2]
        texts=[c["text"] for c in chunks]
        return [{"text":chunks[i]["text"],"title":chunks[i]["title"],"doc_id":chunks[i].get("doc_id",""),"score":round(sc,4)}
                for i,sc in _tfidf(texts,query,top_k) if sc>0.05]

    def check_compliance(self,transcript:str,call_id:str="")->List[Dict]:
        violations=list(self._rule_based(transcript))
        if self._cache and self.llm:
            try:
                rag=self._rag_check(transcript,call_id)
                existing={v["rule_text"] for v in violations}
                for v in rag:
                    if v["rule_text"] not in existing: violations.append(v); existing.add(v["rule_text"])
            except Exception as e: log.warning(f"RAG check failed: {e}")
        if call_id and self.db:
            for v in violations:
                try: self.db.insert_policy_violation(call_id,v.get("doc_id",""),v["rule_text"],v["violation"],v["severity"])
                except: pass
        return violations

    def _rag_check(self,transcript:str,call_id:str)->List[Dict]:
        words=re.findall(r"\b[a-zA-Z]{4,}\b",transcript.lower())
        stop={"that","this","with","from","have","been","will","they","their","there","were","what","when","which"}
        query=" ".join([w for w in dict.fromkeys(words) if w not in stop][:15])
        chunks=self.retrieve(query,top_k=3)
        if not chunks: return []
        context="\n\n".join(f"[{c['title']}]\n{c['text']}" for c in chunks)
        prompt=f"""You are a compliance checker. Find policy violations only.

POLICY:
{context}

TRANSCRIPT:
{transcript[:2500]}

Reply ONLY with JSON array ([] if none):
[{{"rule":"policy rule","violation":"what happened","severity":"high|medium|low"}}]"""
        try:
            import requests as req
            r=req.post("https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization":f"Bearer {os.getenv('OPENROUTER_API_KEY','')}","Content-Type":"application/json"},
                json={"model":os.getenv("OPENROUTER_MODEL","anthropic/claude-3-haiku"),
                      "messages":[{"role":"user","content":prompt}],"max_tokens":500,"temperature":0.1},timeout=25)
            r.raise_for_status()
            raw=r.json()["choices"][0]["message"]["content"]
            clean=re.sub(r"```[a-z]*|```","",raw).strip()
            s,e=clean.find("["),clean.rfind("]")
            if s!=-1 and e!=-1:
                items=json.loads(clean[s:e+1])
                return [{"rule_text":i.get("rule",""),"violation":i.get("violation",""),
                         "severity":i.get("severity","medium"),"doc_id":chunks[0].get("doc_id",""),"source":"rag+llm"}
                        for i in items[:5] if i.get("rule")]
        except Exception as e: log.debug(f"rag_check llm: {e}")
        return []

    # Alias for tests
    def _rule_based_check(self,transcript:str)->List[Dict]:
        return self._rule_based(transcript)

    def _rule_based(self,transcript:str)->List[Dict]:
        tl=transcript.lower(); violations=[]
        for rule_text,pattern,must_have,severity in self.RULES:
            matched=bool(re.search(pattern,tl,re.IGNORECASE))
            if must_have and not matched:
                violations.append({"rule_text":rule_text,"severity":severity,"violation":"Not performed","doc_id":"","source":"rule"})
            elif not must_have and matched:
                violations.append({"rule_text":rule_text,"severity":severity,"violation":"Prohibited action detected","doc_id":"","source":"rule"})
        return violations
