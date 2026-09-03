# Infrastructure clients

This package owns the long-lived connection pools/drivers for the five business
datastores in `compose.yml`:

- `PostgresClient`: asyncpg pool and `connection()` context manager
- `RedisClient`: redis-py asyncio client
- `MinioClient`: official MinIO client; SDK operations are synchronous
- `MilvusClient`: PyMilvus 2.4 client matching the Milvus 2.4.5 server
- `Neo4jClient`: official asynchronous Neo4j driver

`etcd` and `milvus-minio` are private Milvus dependencies and intentionally do
not have application clients.

Use the aggregate lifecycle directly with FastAPI:

```python
from fastapi import FastAPI

from app.infrastructure import infrastructure_lifespan

app = FastAPI(lifespan=infrastructure_lifespan)
```

Access an individual client through its cached getter:

```python
from app.infrastructure import get_postgres_client, get_redis_client

async with get_postgres_client().connection() as connection:
    row = await connection.fetchrow("SELECT 1 AS value")

redis = await get_redis_client().connect()
await redis.set("key", "value")
```

MinIO and Milvus expose their official synchronous SDK clients through the
`client` property. Call blocking SDK operations with `asyncio.to_thread()` when
inside an async request handler.
