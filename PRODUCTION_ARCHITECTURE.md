# Building a Production-Grade Real-Time Call Quality Analysis Platform

## Enterprise Architecture — No Free Tiers, Full Production Stack

This document describes how to rebuild Criterion QA using production-level cloud infrastructure, enterprise APIs, and battle-tested engineering practices. Every component listed here is used by companies processing millions of calls per day.

---

## 1. Architecture Overview

```
                    ┌─────────────────────────────────────┐
                    │         CLIENT LAYER                │
                    │  Web App · Mobile SDK · REST API    │
                    └────────────────┬────────────────────┘
                                     │ HTTPS / WSS
                    ┌────────────────▼────────────────────┐
                    │      API GATEWAY + CDN               │
                    │  AWS API Gateway / Kong Enterprise   │
                    │  Cloudflare (WAF + DDoS protection)  │
                    └────────────────┬────────────────────┘
                                     │
          ┌──────────────────────────▼──────────────────────────┐
          │               BACKEND SERVICES                      │
          │                                                      │
          │  ┌──────────────┐  ┌────────────────┐              │
          │  │ Upload Service│  │ Auth Service   │              │
          │  │ (Python/Go)   │  │ (OAuth 2.0 +   │              │
          │  └──────┬────────┘  │  SAML / OIDC)  │              │
          │         │           └────────────────┘              │
          │         ▼                                            │
          │  ┌──────────────────┐                               │
          │  │   Message Queue  │                               │
          │  │  Amazon SQS /    │                               │
          │  │  Apache Kafka    │                               │
          │  └──────┬───────────┘                               │
          │         │                                            │
          │    ┌────▼──────────┐   ┌────────────────────┐      │
          │    │ Transcription │   │  Scoring Worker    │      │
          │    │ Worker Pool   │   │  (LLM Pipeline)    │      │
          │    │ (Auto-scale)  │   │  (Auto-scale)      │      │
          │    └───────────────┘   └────────────────────┘      │
          └─────────────────────────────────────────────────────┘
                     │                        │
          ┌──────────▼──────┐    ┌────────────▼────────┐
          │  Object Storage │    │  Vector Database    │
          │  AWS S3 / GCS   │    │  Pinecone / pgvector│
          └─────────────────┘    └─────────────────────┘
                     │
          ┌──────────▼────────────────────────────────────────┐
          │              DATA LAYER                           │
          │  PostgreSQL (RDS Multi-AZ)  ·  Redis Cluster     │
          │  ClickHouse (Analytics)     ·  Elasticsearch     │
          └───────────────────────────────────────────────────┘
```

---

## 2. Speech-to-Text: Production Transcription

### Recommended: AWS Transcribe or Google Speech-to-Text Enterprise

**AWS Transcribe (recommended for AWS shops)**
```python
import boto3

client = boto3.client("transcribe", region_name="us-east-1")

# Start a transcription job
client.start_transcription_job(
    TranscriptionJobName=f"call-{call_id}",
    Media={"MediaFileUri": f"s3://your-bucket/calls/{call_id}.mp4"},
    MediaFormat="mp4",
    LanguageCode="en-US",
    Settings={
        "ShowSpeakerLabels": True,     # diarization
        "MaxSpeakerLabels": 2,          # agent + customer
        "ChannelIdentification": True,  # stereo: left=agent, right=customer
        "VocabularyName": "cloudtech-terminology",  # custom vocab
    },
    OutputBucketName="your-transcripts-bucket",
    ContentRedaction={
        "RedactionType": "PII",
        "RedactionOutput": "redacted",  # GDPR compliance
    },
)
```

**Alternatives:**
- **Google Speech-to-Text v2** — Best multilingual accuracy, speaker diarization, 110+ languages
- **Azure Cognitive Services Speech** — Best for Microsoft 365 shops, Teams call recording integration
- **AssemblyAI** — Best real-time streaming, purpose-built for call centres
- **Deepgram Enterprise** — Lowest latency, custom model fine-tuning on your call data

**Custom vocabulary (critical for tech companies):**
```python
# AWS: create a custom vocabulary with your product terminology
client.create_vocabulary(
    VocabularyName="cloudtech-terminology",
    LanguageCode="en-US",
    Phrases=[
        "CloudCore", "SaaS", "API gateway", "Kubernetes",
        "infrastructure as a service", "zero-trust", "SLA",
        "RTO", "RPO", "failover", "multi-region", "compliance",
    ],
)
```

---

## 3. LLM Scoring: Production-Grade Configuration

### Option A: AWS Bedrock (recommended for AWS environments)
No data leaves AWS. Compliant with SOC 2, HIPAA, GDPR.

```python
import boto3, json

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

def score_transcript(transcript: str) -> dict:
    response = bedrock.invoke_model(
        modelId="anthropic.claude-3-5-sonnet-20241022-v2:0",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{
                "role": "user",
                "content": f"Score this support call transcript on 10 quality dimensions:\n\n{transcript}"
            }],
        }),
        contentType="application/json",
    )
    return json.loads(response["body"].read())
```

### Option B: Azure OpenAI (enterprise GPT-4o)
```python
from openai import AzureOpenAI

client = AzureOpenAI(
    azure_endpoint="https://YOUR-RESOURCE.openai.azure.com",
    api_key="YOUR_KEY",
    api_version="2024-10-21",
)

response = client.chat.completions.create(
    model="gpt-4o",          # your deployment name
    messages=[{"role":"user","content": prompt}],
    max_tokens=1024,
    temperature=0.1,
    response_format={"type": "json_object"},   # structured output
)
```

### Option C: Direct Anthropic Claude API (enterprise tier)
```python
import anthropic

client = anthropic.Anthropic(api_key="YOUR_ANTHROPIC_KEY")

message = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=1024,
    messages=[{"role":"user","content": prompt}],
)
```

### LLM Cost Optimisation at Scale
- **Cache embeddings and scores** for repeated transcripts (Redis, ~20% savings)
- **Batch inference**: group 10-50 short calls, send in one API request
- **Smaller models for simple cases**: use Claude Haiku for FCR/sentiment, Claude Opus for complex scoring
- **Fine-tune your own model**: after 50,000+ scored calls, fine-tune Llama 3 70B on your domain — reduces inference cost by 90%

---

## 4. Vector Database: Enterprise RAG

### Production Choice: Pinecone Enterprise

```python
from pinecone import Pinecone, ServerlessSpec

pc = Pinecone(api_key="YOUR_PINECONE_KEY")

# Create index with dimension matching your embedding model
pc.create_index(
    name="policy-documents",
    dimension=1536,     # OpenAI text-embedding-3-large
    metric="cosine",
    spec=ServerlessSpec(cloud="aws", region="us-east-1"),
)

index = pc.Index("policy-documents")

# Upsert policy chunks with metadata
index.upsert(vectors=[
    {
        "id": chunk_id,
        "values": embedding_vector,
        "metadata": {
            "doc_id": doc_id,
            "title": title,
            "text": chunk_text,
            "section": section,
            "version": "3.2",
        }
    }
    for chunk_id, embedding_vector, ... in chunks
])

# Query
results = index.query(
    vector=query_embedding,
    top_k=5,
    filter={"doc_id": {"$in": active_doc_ids}},
    include_metadata=True,
)
```

### Embeddings: OpenAI text-embedding-3-large (3072-dim, best quality)
```python
from openai import OpenAI
client = OpenAI(api_key="YOUR_KEY")

response = client.embeddings.create(
    model="text-embedding-3-large",
    input=["your text chunk here"],
    dimensions=1536,   # truncate to save storage
)
embedding = response.data[0].embedding
```

### Alternatives
- **Weaviate Cloud** — Built-in hybrid search (vector + keyword), GDPR-compliant EU hosting
- **Qdrant Cloud** — Open-source friendly, excellent Rust performance
- **pgvector on RDS PostgreSQL** — Best if you're already on PostgreSQL; no extra service to manage
- **Google Vertex AI Matching Engine** — Best for Google Cloud shops

---

## 5. Databases: Production Schema

### Primary Store: PostgreSQL on AWS RDS (Multi-AZ)
```sql
-- Multi-AZ for HA, read replicas for analytics queries
-- Enable point-in-time recovery, automated backups

-- Partition calls table by month for 90-day retention management
CREATE TABLE calls (
    call_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id    TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
) PARTITION BY RANGE (created_at);

CREATE TABLE calls_2026_03 
    PARTITION OF calls
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

-- Automatically drop old partitions for retention
```

### Analytics: ClickHouse (time-series + OLAP)
```sql
-- ClickHouse handles billions of rows for real-time analytics
CREATE TABLE quality_scores (
    call_id      UUID,
    agent_id     String,
    overall      Float32,
    empathy      Float32,
    created_at   DateTime,
    PRIMARY KEY (agent_id, created_at)
) ENGINE = MergeTree()
ORDER BY (agent_id, created_at)
TTL created_at + INTERVAL 90 DAY;   -- auto data retention
```

### Cache: Redis Enterprise Cluster
```python
import redis.asyncio as redis

pool = redis.ConnectionPool.from_url(
    "redis://your-redis-cluster:6379",
    max_connections=100,
    decode_responses=True,
)
r = redis.Redis(connection_pool=pool)

# Cache quality scores (hot data)
await r.setex(f"qa:{call_id}", 3600, json.dumps(score))

# Cache agent stats (computed every 5 min)
await r.setex(f"agent:{agent_id}:stats", 300, json.dumps(stats))
```

---

## 6. Message Queue: Apache Kafka (Amazon MSK)

```python
from confluent_kafka import Producer, Consumer

# Producer — upload service sends call events
producer = Producer({"bootstrap.servers": "your-msk-cluster:9092"})

producer.produce(
    topic="call.uploaded",
    key=call_id,
    value=json.dumps({
        "call_id": call_id,
        "s3_uri":  f"s3://calls/{call_id}.mp4",
        "agent_id": agent_id,
        "priority": "high",
    }).encode(),
    callback=lambda err, msg: print(f"Delivered to {msg.partition()}" if not err else f"Error: {err}"),
)

# Consumer — transcription worker pool
consumer = Consumer({
    "bootstrap.servers": "your-msk-cluster:9092",
    "group.id":          "transcription-workers",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,   # manual commit for exactly-once
})
consumer.subscribe(["call.uploaded"])
```

**Topic design for your call platform:**
- `call.uploaded` → triggers transcription
- `call.transcribed` → triggers LLM scoring
- `call.scored` → triggers alert evaluation and notification
- `call.completed` → triggers summary analytics update
- `call.failed` → dead-letter queue, triggers system alert

---

## 7. Authentication: Enterprise SSO

```python
# OAuth 2.0 + OIDC with Azure AD / Okta
from fastapi import Depends
from fastapi_azure_auth import MultiTenantAzureAuthorizationCodeBearer

azure_scheme = MultiTenantAzureAuthorizationCodeBearer(
    app_client_id="YOUR_APP_CLIENT_ID",
    scopes={"api://YOUR_APP/user_impersonation": "Access QA Platform"},
    validate_iss=True,
)

@app.get("/api/calls")
async def get_calls(token=Depends(azure_scheme)):
    # token.claims contains user identity, roles, groups
    user_id = token.claims["oid"]
    roles    = token.claims.get("roles", [])
    if "QA.Admin" not in roles:
        raise HTTPException(403)
```

**RBAC roles for a call QA platform:**
- `QA.Agent` — view own call scores only
- `QA.Supervisor` — view team scores, access alerts
- `QA.Admin` — full access, policy management, exports
- `QA.Executive` — read-only summary dashboards
- `System.Automation` — service accounts for batch processing

---

## 8. Infrastructure as Code: Terraform

```hcl
# main.tf — Production AWS setup

# EKS for container orchestration
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  cluster_name    = "criterion-qa-prod"
  cluster_version = "1.29"
  
  node_groups = {
    api_workers = {
      desired_size = 3
      max_size     = 10
      min_size     = 2
      instance_types = ["m6i.xlarge"]
    }
    ml_workers = {
      desired_size = 2
      max_size     = 20   # scale for batch processing
      min_size     = 0
      instance_types = ["g4dn.xlarge"]   # GPU for on-prem LLM
    }
  }
}

# RDS PostgreSQL Multi-AZ
resource "aws_db_instance" "criterion_pg" {
  identifier           = "criterion-prod"
  engine               = "postgres"
  engine_version       = "16.2"
  instance_class       = "db.r6g.2xlarge"
  allocated_storage    = 500
  multi_az             = true
  deletion_protection  = true
  backup_retention_period = 30
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
}

# ElastiCache Redis Cluster
resource "aws_elasticache_replication_group" "criterion_redis" {
  replication_group_id = "criterion-cache"
  node_type            = "cache.r6g.large"
  num_cache_clusters   = 3   # 1 primary + 2 replicas
  automatic_failover_enabled = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
}
```

---

## 9. Observability: Production Monitoring Stack

### Metrics: Prometheus + Grafana (or Datadog)

```python
from prometheus_client import Counter, Histogram, Gauge

CALLS_PROCESSED = Counter(
    "calls_processed_total",
    "Total calls processed",
    ["status", "agent_tier"],
)
TRANSCRIPTION_LATENCY = Histogram(
    "transcription_duration_seconds",
    "Time to transcribe a call",
    buckets=[5, 10, 30, 60, 120, 300],
)
ACTIVE_QUEUE_DEPTH = Gauge(
    "queue_depth_active",
    "Current messages in the processing queue",
)

# Instrument your code
@TRANSCRIPTION_LATENCY.time()
def transcribe(audio_path: str) -> dict:
    result = call_transcription_api(audio_path)
    CALLS_PROCESSED.labels(status="success", agent_tier="platinum").inc()
    return result
```

### Distributed Tracing: OpenTelemetry → Jaeger / AWS X-Ray
```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="http://otel-collector:4317")))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("criterion.pipeline")

def process_call(call_id: str):
    with tracer.start_as_current_span("process_call") as span:
        span.set_attribute("call.id", call_id)
        with tracer.start_as_current_span("transcribe"):
            transcript = transcribe_audio(call_id)
        with tracer.start_as_current_span("score"):
            scores = score_transcript(transcript)
```

### Alerting: PagerDuty + Slack
```python
import pdpyras  # PagerDuty Python SDK

session = pdpyras.EventsAPISession("YOUR_ROUTING_KEY")

# Trigger a P1 incident — SLA breach
session.trigger(
    summary=f"SLA breach: call {call_id} processing >120s",
    source="criterion-qa",
    severity="critical",
    custom_details={
        "call_id":    call_id,
        "agent_id":   agent_id,
        "elapsed_s":  elapsed,
        "sla_target": 60,
    },
)
```

---

## 10. Security: Production Hardening

### Encryption
- **In transit**: TLS 1.3 everywhere; mTLS between internal microservices
- **At rest**: AES-256 with AWS KMS (rotate keys every 90 days)
- **Audio files**: Server-side encryption on S3 with customer-managed keys (SSE-KMS)
- **Database**: Transparent Data Encryption on RDS; encrypted parameter store for secrets

### Secrets Management: AWS Secrets Manager
```python
import boto3
import json

def get_secret(secret_name: str) -> dict:
    client = boto3.client("secretsmanager", region_name="us-east-1")
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])

# Usage — no hardcoded credentials
secrets = get_secret("prod/criterion/api-keys")
DEEPGRAM_KEY   = secrets["deepgram_api_key"]
ANTHROPIC_KEY  = secrets["anthropic_api_key"]
```

### PII Redaction (GDPR compliance)
```python
import boto3

comprehend = boto3.client("comprehend", region_name="us-east-1")

def redact_pii(text: str) -> str:
    response = comprehend.detect_pii_entities(Text=text, LanguageCode="en")
    # Replace PII with type labels
    for entity in sorted(response["Entities"], key=lambda x: x["BeginOffset"], reverse=True):
        placeholder = f"[{entity['Type']}]"
        text = text[:entity["BeginOffset"]] + placeholder + text[entity["EndOffset"]:]
    return text
```

---

## 11. Deployment: CI/CD Pipeline

### GitHub Actions → ECR → EKS
```yaml
# .github/workflows/deploy.yml
name: Deploy Criterion QA

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::ACCOUNT:role/GithubActionsRole
          aws-region: us-east-1
          
      - name: Build and push Docker image
        run: |
          aws ecr get-login-password | docker login --username AWS --password-stdin $ECR_REGISTRY
          docker build -t criterion-backend:${{ github.sha }} .
          docker push $ECR_REGISTRY/criterion-backend:${{ github.sha }}
          
      - name: Deploy to EKS
        run: |
          aws eks update-kubeconfig --name criterion-qa-prod
          kubectl set image deployment/criterion-api \
            api=$ECR_REGISTRY/criterion-backend:${{ github.sha }}
          kubectl rollout status deployment/criterion-api --timeout=300s
          
      - name: Run smoke tests
        run: |
          kubectl run smoke-test --image=curlimages/curl --restart=Never \
            --command -- curl -f https://api.criterion-qa.com/api/health
```

---

## 12. Cost Estimate at Scale (10,000 calls/day, avg 8 minutes each)

| Component | Service | Monthly Cost (USD) |
|-----------|---------|------------------:|
| Transcription | AWS Transcribe | ~$12,000 |
| LLM Scoring | AWS Bedrock (Claude 3.5 Sonnet) | ~$8,000 |
| Embeddings | OpenAI text-embedding-3-large | ~$600 |
| Vector DB | Pinecone Standard pod | ~$700 |
| Audio Storage | S3 Standard + Glacier (90d) | ~$1,200 |
| PostgreSQL | RDS r6g.2xlarge Multi-AZ | ~$1,400 |
| Redis Cache | ElastiCache r6g.large x3 | ~$800 |
| Kafka | Amazon MSK (3 broker) | ~$900 |
| EKS Compute | 8x m6i.xlarge On-Demand | ~$2,400 |
| Monitoring | Datadog APM + Logs | ~$1,500 |
| CDN + WAF | Cloudflare Enterprise | ~$1,000 |
| **TOTAL** | | **~$30,500/month** |

**Cost reduction strategies:**
- Use Spot instances for transcription workers (70% saving on compute)
- Fine-tune a smaller open-source LLM on your scored calls after 6 months (eliminates LLM API cost)
- Use S3 Intelligent-Tiering (auto moves old audio to cheaper storage)
- Reserved instances for baseline compute (1-year commitment, 40% saving)
- Cache LLM results for duplicate or near-duplicate transcripts

---

## 13. Compliance Certifications to Target

| Standard | Relevance | Path to Certification |
|----------|-----------|----------------------|
| SOC 2 Type II | Data security for enterprise customers | AWS inherits most controls; audit annually |
| ISO 27001 | Information security management | Implement ISMS, 6-month audit process |
| GDPR | EU personal data handling | Data residency in EU regions, DPA, right-to-erasure |
| PCI DSS | If payment data in calls | Scope reduction via tokenisation |
| HIPAA | Healthcare calls | AWS HIPAA BAA, PHI encryption |

---

## 14. Key Architecture Differences: Free vs Production

| Dimension | Free / Dev (current) | Production Enterprise |
|-----------|---------------------|-----------------------|
| Transcription | Deepgram free tier | AWS Transcribe / Google STT with custom vocabulary |
| LLM | OpenRouter claude-3-haiku | AWS Bedrock Claude 3.5 Sonnet / Azure OpenAI GPT-4o |
| Embeddings | sentence-transformers (local) | OpenAI text-embedding-3-large |
| Vector DB | In-memory / SQLite | Pinecone Enterprise / Weaviate Cloud |
| Primary DB | SQLite | PostgreSQL RDS Multi-AZ |
| Analytics DB | None | ClickHouse |
| Cache | None | Redis Enterprise Cluster |
| Queue | In-process threading | Apache Kafka (Amazon MSK) |
| Auth | None | OAuth 2.0 + OIDC (Okta / Azure AD) |
| Secrets | .env file | AWS Secrets Manager |
| Compute | Local Flask dev server | EKS (Kubernetes) with HPA |
| CI/CD | Manual | GitHub Actions → ECR → EKS with rollback |
| Monitoring | Log files | Datadog / Prometheus + Grafana + PagerDuty |
| Tracing | None | OpenTelemetry → AWS X-Ray / Jaeger |
| Storage | Local disk | S3 with KMS encryption and lifecycle policies |
| Compliance | None | SOC 2, GDPR, ISO 27001 |
| Availability | Single process | 99.99% SLA, multi-AZ, auto-failover |

---

*Built for CloudTech Solutions · Enterprise Engineering Team · March 2026*
