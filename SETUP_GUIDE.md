# Criterion QA v3 — Integration Setup Guide

## 1. SUPABASE DATABASE (required for production)

### Step 1: Create a Supabase project
1. Go to https://supabase.com → **New Project**
2. Choose a name, password, region → **Create Project** (wait ~2 min)

### Step 2: Get your credentials
- Go to **Settings → API**
- Copy `Project URL` → paste as `SUPABASE_URL` in `config/.env`
- Copy `anon public` key → paste as `SUPABASE_ANON_KEY`

### Step 3: Run the schema (ONE TIME)
1. Go to **SQL Editor → New Query**
2. Open `backend/database/setup_supabase.sql`
3. Copy all contents → paste → click **Run**
4. You should see 10 table names in the result

### Step 4: Verify
```
curl http://localhost:5000/api/health
# Should show: "database": "supabase"
```

---

## 2. SLACK NOTIFICATIONS (two channels)

### Step 1: Create a Slack App
1. Go to https://api.slack.com/apps → **Create New App**
2. Choose **From scratch**
3. Name: `Criterion QA Alerts`, pick your workspace

### Step 2: Add Incoming Webhooks
1. In your app sidebar: **Incoming Webhooks**
2. Toggle **Activate Incoming Webhooks** → ON
3. Click **Add New Webhook to Workspace**

### Channel 1 — Agent/QA Supervisor Alerts
1. Select channel: `#qa-alerts` (or create it first)
2. Click **Allow**
3. Copy the webhook URL (starts with `https://hooks.slack.com/services/...`)
4. Paste into `config/.env` as `AGENT_SLACK_WEBHOOK_URL`

### Channel 2 — System/IT Alerts
1. Click **Add New Webhook to Workspace** again
2. Select channel: `#system-alerts` (or `#devops`)
3. Click **Allow**
4. Copy the webhook URL
5. Paste into `config/.env` as `SYSTEM_SLACK_WEBHOOK_URL`

### Test it
```bash
# From your terminal:
curl -X POST http://localhost:5000/api/notifications/dual/agent
curl -X POST http://localhost:5000/api/notifications/dual/system
# Check your Slack channels
```

---

## 3. EMAIL NOTIFICATIONS (Resend — 3,000 free emails/month)

### Step 1: Sign up
1. Go to https://resend.com → Sign up free
2. Verify your domain (or use their test domain for dev)

### Step 2: Get API key
1. Go to **API Keys → Create API Key**
2. Name: `Criterion QA`
3. Copy the key → paste as `RESEND_API_KEY` in `config/.env`

### Step 3: Set recipient emails
```env
AGENT_EMAIL_TO=qa-supervisor@yourcompany.com
SYSTEM_EMAIL_TO=devops@yourcompany.com
ALERT_EMAIL_FROM=criterion@yourdomain.com
```

### For multiple recipients (comma-separated):
```env
AGENT_EMAIL_TO=supervisor@company.com,qa-lead@company.com
```

---

## 4. VECTOR EMBEDDINGS (for better RAG)

### Auto-installs on first use
```bash
pip install sentence-transformers
```
The model (`all-MiniLM-L6-v2`, ~90MB) downloads automatically on first policy upload.

### Enable pgvector in Supabase (for cloud vector search)
Run in Supabase SQL Editor:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### Test embeddings are working
```bash
curl -X POST http://localhost:5000/api/policy \
  -F "title=Test Policy" \
  -F "file=@/path/to/your/policy.txt"

# Then search:
curl "http://localhost:5000/api/policy/search?q=how+should+agent+greet+customer"
```

---

## 5. OPENROUTER / LLM

### Already configured with your key
```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=anthropic/claude-3-haiku
```

### Free Mistral fallback (1M tokens/month free)
1. Go to https://console.mistral.ai → Sign up
2. **API Keys → Create new key**
3. Paste as `MISTRAL_API_KEY`

---

## 6. DEEPGRAM

### Already configured with your key
The `nova-2` model with `diarize=true` handles:
- Two speakers on the same channel ✓
- First-to-speak = agent detection ✓
- Free tier: 45,000 minutes/year

### To test:
```bash
curl -X POST http://localhost:5000/api/upload \
  -F "file=@call_log_1.m4a" \
  -F "agent_id=agent_001"
```

---

## 7. COMPLETE config/.env REFERENCE

```env
# ── REQUIRED ─────────────────────────────────────────────
DEEPGRAM_API_KEY=d17e97034ff7...          # your key
OPENROUTER_API_KEY=sk-or-v1-...          # your key
OPENROUTER_MODEL=anthropic/claude-3-haiku
LLM_PROVIDER=openrouter

# ── SUPABASE (production DB) ──────────────────────────────
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGci...

# ── SLACK (two channels) ──────────────────────────────────
AGENT_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx
SYSTEM_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../yyy

# ── EMAIL (Resend) ────────────────────────────────────────
RESEND_API_KEY=re_...
ALERT_EMAIL_FROM=criterion@yourdomain.com
AGENT_EMAIL_TO=qa@company.com
SYSTEM_EMAIL_TO=devops@company.com

# ── OPTIONAL ──────────────────────────────────────────────
ANTHROPIC_API_KEY=                        # direct Anthropic fallback
MISTRAL_API_KEY=                          # free 1M tokens/month
ALERT_THROTTLE_MINUTES=5
BATCH_WORKERS=20
LOG_LEVEL=INFO
EMBEDDING_MODEL=all-MiniLM-L6-v2
```

---

## 8. QUICK START (local, no cloud needed)

```bash
cd criterion_v3/backend

# Install dependencies
pip install -r requirements.txt

# (optional) Install embeddings model
pip install sentence-transformers

# Set only Deepgram + OpenRouter keys in config/.env
# Leave Supabase blank → uses SQLite automatically

python app.py
# Open http://localhost:5000
```

---

## 9. PRODUCTION DEPLOYMENT (Vercel)

```bash
# Install Vercel CLI
npm install -g vercel

# Deploy
cd criterion_v3
vercel

# Set environment variables in Vercel dashboard:
# vercel.com → Project → Settings → Environment Variables
# Add all keys from config/.env
```

---

## 10. Alert Trigger Summary

| Alert | Channel | Trigger |
|-------|---------|---------|
| Low quality score | Agent/QA | overall_rating < 5.0 |
| Unparliamentary language | Agent/QA | Banned words detected |
| Sentiment escalation | Agent/QA | Customer sentiment drops sharply |
| Policy violation | Agent/QA | Rule/RAG compliance check fails |
| Low compliance score | Agent/QA | compliance metric < 4.0 |
| Transcription failure | System | Deepgram returns empty/error |
| LLM provider failure | System | All LLM providers failed |
| SLA breach | System | Processing > 60s |
| DB connection error | System | Supabase unreachable |
| High DLQ depth | System | >10 failed jobs in queue |
