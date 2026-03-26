# Criterion QA — AI-Powered Call Quality Analysis Platform
### Infosys Internship Project | Rameshwar SP

---

## Slide 1 — Title Slide

**Project Name:** Criterion QA v3
**Tagline:** Analyze. Score. Improve. — AI-powered quality auditing for customer support calls.
**Presented by:** Rameshwar SP
**Organization:** Infosys Internship Assignment
**Live URL:** https://criterionv3updated.vercel.app
**GitHub:** https://github.com/Rameshwarsp7900/Infosys-Internship-assignment-

---

## Slide 2 — Problem Statement

Customer support teams handle hundreds of calls daily.
Manual QA review is:
- Slow — reviewers can only audit 2–5% of calls
- Inconsistent — subjective scoring varies by reviewer
- Delayed — feedback reaches agents days later
- Incomplete — no sentiment, compliance, or policy checks

**Result:** Poor agent performance goes undetected. Customer satisfaction drops.

---

## Slide 3 — Solution Overview

**Criterion QA** automates the entire quality review pipeline using AI.

- Upload a call recording or chat log
- AI transcribes, scores, and analyzes it in seconds
- Agents and supervisors get instant, objective feedback
- Alerts fire automatically for critical issues
- All data tracked over time in a live dashboard

**From upload to full QA report in under 60 seconds.**

---

## Slide 4 — Key Features

| Feature | Description |
|---|---|
| Single Call Analysis | Upload MP3/WAV/TXT → instant AI quality report |
| Batch Processing | Analyze up to 500 calls simultaneously |
| Agent Dashboard | Track scores, trends, and performance over time |
| Alert Center | Real-time alerts for low scores, escalations, violations |
| Policy Engine (RAG) | Upload company policy docs — every call checked against them |
| Sentiment Analysis | Detect customer/agent sentiment and escalation points |
| Chatbot Assistant | Ask questions about agents and platform data live |
| Call History | Full searchable history with transcripts and scores |

---

## Slide 5 — System Architecture

```
User (Browser)
      │
      ▼
Frontend (HTML + CSS + JS)
      │  REST API calls
      ▼
Flask Backend (Python)
      │
      ├── Transcription Layer
      │     └── Deepgram nova-2 (speech-to-text + speaker diarization)
      │
      ├── LLM Scoring Layer
      │     └── OpenRouter → Claude 3 Haiku (primary)
      │     └── Mistral AI (fallback)
      │
      ├── RAG Policy Engine
      │     └── PDF/TXT policy upload → chunk → keyword/vector search
      │
      ├── Sentiment Analyzer
      │     └── TextBlob + timeline analysis
      │
      ├── Alert Engine
      │     └── Rule-based triggers → Slack + Email notifications
      │
      └── Database Layer
            └── SQLite (local dev) / Supabase PostgreSQL (production)
```

---

## Slide 6 — Tech Stack

**Backend**
- Python 3.11 + Flask 3.0
- Deepgram nova-2 — speech transcription + diarization
- OpenRouter / Claude 3 Haiku — LLM quality scoring
- Mistral AI — fallback LLM
- TextBlob — sentiment analysis
- PyPDF — PDF policy parsing
- Supabase (PostgreSQL) — cloud database
- SQLite — local development database

**Frontend**
- Vanilla HTML5 + CSS3 + JavaScript
- No framework — lightweight, fast, zero build step

**Infrastructure**
- Vercel — serverless deployment
- GitHub — version control + CI/CD
- Resend — transactional email
- Slack Webhooks — real-time notifications

---

## Slide 7 — Quality Metrics (Scored 1–10)

Every call is scored across 10 dimensions by the LLM:

| Metric | What It Measures |
|---|---|
| Empathy | Emotional understanding and compassion shown |
| Communication | Clarity, tone, and effectiveness |
| Resolution | Was the customer's problem actually solved? |
| Compliance | Adherence to company policy and scripts |
| Customer Satisfaction | Predicted satisfaction score |
| Professionalism | Conduct, language, and demeanor |
| Product Knowledge | Accuracy of information provided |
| Listening | Active listening and acknowledgment |
| Response Time | Speed and efficiency of responses |
| First Call Resolution | Problem solved without a callback |

**Plus:** F1 Score (precision/recall harmonic mean) for overall accuracy.

---

## Slide 8 — Transcription Pipeline

```
Audio File (MP3/WAV/M4A)
        │
        ▼
  File > 10 min?
   Yes → Split into 10-min chunks (pydub + ffmpeg)
   No  → Send directly to Deepgram
        │
        ▼
  Deepgram nova-2 API
  - Speaker diarization (who said what)
  - Punctuation + smart formatting
  - Utterance splitting (1.5s silence threshold)
        │
        ▼
  Speaker Assignment
  - Agent = speaker with most total talk time
  - Customer = remaining speaker(s)
        │
        ▼
  Segment Merging
  - Consecutive same-speaker segments merged (< 0.5s gap)
  - Chunk boundaries re-merged to avoid false turn splits
        │
        ▼
  Formatted Transcript → LLM Scoring
```

**Supports:** MP3, M4A, WAV, OGG, FLAC, WEBM, TXT chat logs

---

## Slide 9 — Alert System

**7 Alert Types:**

| Alert | Trigger | Severity |
|---|---|---|
| Low Quality Score | Overall score < threshold | High |
| Low Empathy | Empathy score < threshold | Medium |
| Low Compliance | Compliance score < threshold | High |
| Sentiment Escalation | Customer sentiment turns negative | Critical |
| Unparliamentary Language | Offensive words detected | Critical |
| Policy Violation | Call violates uploaded policy rules | High |
| Processing Failure | Pipeline error during analysis | Medium |

**Dual-channel delivery:**
- Channel 1 (Agent/QA): Slack + Email to QA supervisors
- Channel 2 (System/IT): Slack + Email to dev/ops team
- Throttle: max 1 alert per agent per 5 minutes (configurable)

---

## Slide 10 — RAG Policy Engine

**How it works:**

1. Supervisor uploads company policy document (PDF or TXT)
2. Document is chunked into searchable segments and stored in DB
3. On every call, the transcript is checked against all policy chunks
4. LLM identifies specific violations with severity ratings
5. Violations appear in the call report and trigger alerts

**Example policies supported:**
- Call greeting scripts ("Must say company name within 10 seconds")
- Prohibited language lists
- Escalation procedures
- Compliance and regulatory requirements
- Product return/refund policies

---

## Slide 11 — Agent Dashboard & Chatbot

**Agent Dashboard shows:**
- Average quality score over time (trend chart)
- Total calls analyzed
- Active alerts count
- Per-metric breakdown (empathy, compliance, etc.)
- Full call history with scores

**AI Chatbot Assistant:**
- Powered by Mistral via OpenRouter
- Pulls live agent data from the database on every query
- Can answer: "Who is the top performer?", "Which agent has the most alerts?", "What is agent_001's average compliance score?"
- Also answers platform how-to questions

---

## Slide 12 — Database Schema

**7 Tables:**

| Table | Purpose |
|---|---|
| agents | Agent profiles, avg scores, call counts |
| calls | Call metadata, file info, processing status |
| quality_scores | All 10 metric scores per call |
| sentiment_analysis | Sentiment timeline, escalation data |
| transcripts | Full text + speaker segments |
| alerts | All alerts with status and severity |
| policy_documents | Uploaded policy docs and chunks |

**Dual DB support:** SQLite for local dev, Supabase PostgreSQL for production (auto-detected via `VERCEL=1` env var)

---

## Slide 13 — Batch Processing

- Upload up to **500 files** in one batch
- Two modes:
  - **Single Agent** — all files attributed to one agent
  - **Multi Agent** — filename used as agent ID (one agent per file)
- Processed concurrently via `ThreadPoolExecutor` (20 workers default)
- Throughput: ~120 calls/minute
- 500 calls processed in ~4–5 minutes
- Each job tracked individually — partial failures don't stop the batch
- Results returned with per-file status, scores, and warnings

---

## Slide 14 — Deployment & DevOps

**Hosting:** Vercel (serverless, global CDN)
**Database:** Supabase (managed PostgreSQL, free tier)
**CI/CD:** GitHub → Vercel auto-deploy on push to master

**Environment config:**
- All secrets stored as Vercel environment variables
- `.env` file gitignored — never committed
- `.env.example` provided for local setup

**Vercel config (`vercel.json`):**
```json
{
  "builds": [{ "src": "backend/app.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "backend/app.py" }],
  "env": { "VERCEL": "1" }
}
```

**Live health check:** `GET /api/health`
```json
{
  "status": "healthy",
  "apis_ready": true,
  "transcription": "ready",
  "scoring": "ready",
  "db": "connected"
}
```

---

## Slide 15 — API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | System health check |
| POST | `/api/upload` | Sync single call analysis |
| POST | `/api/upload/async` | Async upload (returns job_id) |
| POST | `/api/batch` | Batch process up to 500 files |
| GET | `/api/agents` | List all agents with stats |
| GET | `/api/agents/<id>` | Agent detail + call history |
| GET | `/api/calls/<id>` | Full call report |
| GET | `/api/alerts` | Active alerts |
| POST | `/api/policy/upload` | Upload policy document |
| GET | `/api/analytics/summary` | Platform-wide analytics |
| POST | `/api/chatbot` | AI assistant query |
| GET | `/api/logs` | Processing logs |

---

## Slide 16 — Challenges & Solutions

| Challenge | Solution |
|---|---|
| Speaker swapping in transcription | Switched from "first to speak = agent" to "most talk time = agent" — agents dominate call duration |
| Chunk boundary speaker splits | Added post-merge pass to collapse same-speaker segments across chunk seams |
| LLM API failures | Multi-LLM fallback chain: Claude → OpenRouter → Mistral → static fallback |
| Vercel 250MB size limit | Excluded `sentence-transformers` (800MB torch dependency); RAG uses keyword search on Vercel |
| SQLite on serverless | Auto-detects Vercel env → uses `/tmp/criterion.db` (ephemeral) or Supabase |
| Batch concurrency | ThreadPoolExecutor capped at 50 workers max to respect API rate limits |

---

## Slide 17 — Results & Impact

- Full QA report generated in **< 60 seconds** per call
- Supports **500 concurrent calls** in batch mode
- **10 quality dimensions** scored objectively per call
- **Zero manual review** needed for flagged calls — alerts auto-fire
- **100% call coverage** vs. 2–5% with manual review
- Live at: **https://criterionv3updated.vercel.app**

---

## Slide 18 — Future Enhancements

- Real-time call monitoring (WebSocket streaming)
- Custom scoring rubrics per team/department
- Agent coaching suggestions generated by LLM
- Multi-language transcription support
- Mobile app for supervisors
- Integration with CRM systems (Salesforce, Zendesk)
- Voice of Customer (VoC) trend analysis
- Automated weekly performance reports via email

---

## Slide 19 — Summary

**Criterion QA** transforms how customer support teams manage quality.

- From manual, slow, inconsistent reviews
- To automated, instant, objective AI-powered analysis

**Built with:** Python · Flask · Deepgram · Claude AI · Mistral · Supabase · Vercel

**Key numbers:**
- 500 calls/batch · 10 metrics · 7 alert types · 12 API endpoints · 7 DB tables

**Live and deployed:** https://criterionv3updated.vercel.app

---

## Slide 20 — Thank You

**Rameshwar SP**
Infosys Internship Assignment

GitHub: https://github.com/Rameshwarsp7900/Infosys-Internship-assignment-
Live Demo: https://criterionv3updated.vercel.app

*"Every call tells a story. Criterion QA makes sure you hear it."*
