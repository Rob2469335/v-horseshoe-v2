class MemoryRetriever:
    def __init__(self, client):
        self.client = client

    def search(self, query, limit=5):
        return {"matches": []}
