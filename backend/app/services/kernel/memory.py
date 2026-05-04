"""
Semantic Memory — stores CIO decisions as vector embeddings for future retrieval.

Before each CIO call, retrieves similar past setups and injects them into the
prompt so the system improves over time.

Dependencies (optional — degrades gracefully if unavailable):
  qdrant-client: uv add qdrant-client
  ollama:        uv add ollama         (for nomic-embed-text embeddings)
  OR use sentence-transformers as an embedding fallback

Qdrant must be running locally:
  docker run -p 6333:6333 qdrant/qdrant

Set QDRANT_URL in .env to override default (http://localhost:6333).
Set EMBED_MODEL to override embedding model (default: nomic-embed-text).
"""

import os
import uuid
import threading
import json
from datetime import datetime, timezone
from typing import List, Tuple, Optional, Dict, Any

QDRANT_URL   = os.environ.get('QDRANT_URL', 'http://localhost:6333')
EMBED_MODEL  = os.environ.get('EMBED_MODEL', 'nomic-embed-text')
COLLECTION   = 'phoenixtrade_memory'
VECTOR_DIM   = 768    # nomic-embed-text output dimension


class MemoryManager:
    """
    Semantic vector memory for CIO decisions.
    Uses qdrant-client + ollama for embeddings.
    Degrades gracefully — if Qdrant/Ollama are unavailable, all ops are no-ops.
    """

    def __init__(self):
        self._lock    = threading.RLock()
        self._client  = None    # qdrant_client.QdrantClient
        self._ready   = False
        threading.Thread(target=self._init, daemon=True).start()

    def _init(self):
        """Background init — does not block app startup."""
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            client = QdrantClient(url=QDRANT_URL, timeout=5)
            # Ensure collection exists
            collections = [c.name for c in client.get_collections().collections]
            if COLLECTION not in collections:
                client.create_collection(
                    collection_name=COLLECTION,
                    vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
                )
            with self._lock:
                self._client = client
                self._ready  = True
        except Exception as e:
            # Qdrant not available — memory ops become no-ops
            pass

    # ── Embedding ─────────────────────────────────────────────────────────────

    def _embed(self, text: str) -> Optional[List[float]]:
        """
        Get embedding vector for text.
        Tries: ollama (nomic-embed-text) → sentence-transformers fallback.
        """
        # Try ollama first
        try:
            import ollama
            resp = ollama.embeddings(model=EMBED_MODEL, prompt=text)
            return resp['embedding']
        except Exception:
            pass

        # Fallback: sentence-transformers (smaller, no server needed)
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer('all-MiniLM-L6-v2')
            vec = model.encode(text).tolist()
            # Pad/truncate to VECTOR_DIM if needed
            if len(vec) < VECTOR_DIM:
                vec = vec + [0.0] * (VECTOR_DIM - len(vec))
            return vec[:VECTOR_DIM]
        except Exception:
            pass

        return None

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add_memory(self, content: str, agent_id: str = 'CIO',
                   metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        Embed content and store in Qdrant. Returns point ID.
        """
        if not self._ready:
            return None
        try:
            from qdrant_client.models import PointStruct

            point_id  = str(uuid.uuid4())
            embedding = self._embed(content)
            if not embedding:
                return None

            payload = {
                'content':    content,
                'agent_id':   agent_id,
                'created_at': datetime.now(timezone.utc).isoformat(),
                **(metadata or {}),
            }

            with self._lock:
                self._client.upsert(
                    collection_name=COLLECTION,
                    points=[PointStruct(id=point_id, vector=embedding, payload=payload)],
                )
            return point_id
        except Exception:
            return None

    def retrieve_memory(self, query: str, n: int = 5) -> List[Tuple[dict, float]]:
        """
        Semantic similarity search. Returns list of (payload, score) tuples.
        """
        if not self._ready:
            return []
        try:
            embedding = self._embed(query)
            if not embedding:
                return []

            with self._lock:
                results = self._client.search(
                    collection_name=COLLECTION,
                    query_vector=embedding,
                    limit=n,
                    with_payload=True,
                )
            return [(r.payload, r.score) for r in results]
        except Exception:
            return []

    def remove_memory(self, point_id: str) -> bool:
        """Delete a memory point by ID."""
        if not self._ready:
            return False
        try:
            from qdrant_client.models import PointIdsList

            with self._lock:
                self._client.delete(
                    collection_name=COLLECTION,
                    points_selector=PointIdsList(points=[point_id]),
                )
            return True
        except Exception:
            return False

    def is_ready(self) -> bool:
        return self._ready

    # ── CIO-specific helpers ──────────────────────────────────────────────────

    def store_cio_decision(self, ticker: str, price: float, verdict: str,
                           thesis: str, technicals: str, outcome: Optional[str] = None):
        """
        Store a CIO decision with full context for future retrieval.
        Called after every invest_analysis run.
        """
        content = (
            f"Ticker: {ticker} | Price: {price:.2f} | Verdict: {verdict}\n"
            f"Technicals: {technicals}\n"
            f"Thesis: {thesis}\n"
            f"Outcome: {outcome or 'pending'}"
        )
        self.add_memory(
            content=content,
            agent_id='CIO',
            metadata={'ticker': ticker, 'verdict': verdict, 'price': price,
                      'outcome': outcome or 'pending'},
        )

    def get_similar_setups(self, ticker: str, technicals: str, n: int = 3) -> str:
        """
        Find past CIO decisions similar to the current setup.
        Returns a formatted string to inject into the CIO prompt.
        """
        query   = f"Ticker: {ticker}\nTechnicals: {technicals}"
        results = self.retrieve_memory(query, n=n)
        if not results:
            return ""

        lines = ["## Similar Past Setups (from memory)"]
        for payload, score in results:
            lines.append(
                f"- [{score:.2f}] {payload.get('ticker','?')} @ {payload.get('price','?')}: "
                f"{payload.get('verdict','?')} — Outcome: {payload.get('outcome','pending')}\n"
                f"  {payload.get('content','')[:200]}"
            )
        return '\n'.join(lines)


# Process-wide singleton
memory_manager = MemoryManager()
