from qdrant_client import QdrantClient, models


class QdrantStore:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client or QdrantClient(url=settings.qdrant_url, timeout=settings.qdrant_timeout_seconds)

    def ensure_collection(self):
        name = self.settings.qdrant_collection_alias
        if not self.client.collection_exists(name):
            self.client.create_collection(name, vectors_config={self.settings.qdrant_dense_vector_name: models.VectorParams(size=self.settings.embedding_dimension, distance=models.Distance.COSINE)})

    def upsert(self, points):
        self.client.upsert(self.settings.qdrant_collection_alias, points=models.Batch(ids=[p[0] for p in points], vectors={self.settings.qdrant_dense_vector_name: [p[1] for p in points]}, payloads=[p[2] for p in points]))

    def search(self, vector, limit):
        return self.client.query_points(collection_name=self.settings.qdrant_collection_alias, query=vector, using=self.settings.qdrant_dense_vector_name, limit=limit).points

