# Telecom Support Chatbox

A small telecom support demo with a responsive FAQ chatbox, a searchable local knowledge base, and an optional LiveKit voice agent.

## Requirements

- Python 3.10 or newer
- Docker Desktop (to run the local Qdrant server)
- An OpenAI API key or Groq API key for the LangChain support agent
- LiveKit Cloud project credentials to use voice
- Internet access in the browser for the LiveKit JavaScript SDK and web fonts

## Run the chatbox

From the project folder, create and activate a virtual environment, then install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Start Qdrant, which stores the searchable knowledge vectors:

```powershell
docker compose up -d qdrant
```

Start the web app:

```powershell
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000). Text chat does not require LiveKit credentials, but it does require an OpenAI or Groq API key for the LangChain agent.

## DarkVeil background

The static frontend includes a WebGL2 DarkVeil background adapted from the supplied React Bits component source, so it does not require a React build or React Bits license key. Browsers without WebGL2 use the animated CSS background instead. The effect is decorative, ignores pointer input, and respects the browser's reduced-motion setting.

## LangChain support agent

Copy `.env.example` to `.env` and configure either `OPENAI_API_KEY`, or `GROQ_API_KEY`. Groq uses its OpenAI-compatible API endpoint and `llama-3.3-70b-versatile` by default; set `GROQ_MODEL` to another active model ID from your Groq Models page if needed, and override `GROQ_BASE_URL` only if required. The OpenAI default chat model is `gpt-4o-mini`; set `LANGCHAIN_MODEL` or `OPENAI_BASE_URL` for an alternate OpenAI-compatible endpoint. The web chat uses a LangChain agent (`create_agent`) with a Qdrant knowledge-search tool. The LiveKit voice agent calls that same LangChain agent for knowledge-backed answers. Text and voice AI responses require an available model endpoint and may incur provider charges.

The shared agent includes tools for searching plans, getting plan details, recommending from a documented plan catalog, checking network status, checking a prepaid balance, and requesting escalation. Plan tools only use indexed plan documents (PDF filenames containing `plan`, `tariff`, or `pricing` are categorized as plans). The included FAQ catalog has clearly labeled illustrative demo plans and prices; they are not real carrier offers and are not available to purchase. Replace these entries with your carrier's verified catalog before presenting prices as real.

Account balance and live network checks require a real provider integration. Set `TELECOM_API_URL` to a trusted provider service that accepts `POST /balance` with `{"phone_number":"<10-digit Indian number>"}` and returns `{"balance":123.45,"currency":"INR"}`; live network checks use `POST /network` with `{"location":"<area>"}` and expect at least `{"status":"..."}`. Set `TELECOM_API_KEY` if that service uses a Bearer API key. Without this integration, balance checks clearly say the service is unavailable and network checks only return published knowledge-base information, not live outage data.

To create real support tickets, configure `TELECOM_ESCALATION_URL` with a trusted webhook that accepts `{"issue_summary":"..."}` and returns a string `ticket_id`. Without it, the agent states that no ticket was created and gives `SUPPORT_CONTACT` if configured. Never put phone numbers, passwords, or verification codes in escalation summaries.

## Enable LiveKit voice

1. Add your LiveKit URL, API key, and API secret to `.env`, and configure either OpenAI or Groq for LangChain.
2. Start the voice agent in a second terminal from the project folder:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   python agent.py dev
   ```

3. Restart the web app and use the phone button for a live voice call. The web server creates a short-lived room token and explicitly dispatches the `telecom-support` agent into that room. The call screen shows whether Nova is listening or speaking and has an End call control.

Use the separate microphone button to record a short dictation, then tap it again to transcribe with Groq Whisper or OpenAI speech-to-text; review the text before sending. Set `GROQ_STT_MODEL` to choose a Groq transcription model (defaults to `whisper-large-v3-turbo`) or `OPENAI_STT_MODEL` for OpenAI (defaults to `whisper-1`). The audio is sent from the app server to the configured provider and limited to 25 MB. Speaker buttons on assistant messages read replies aloud using browser speech synthesis. LiveKit Inference provides the realtime voice conversation. Voice tool calls query Qdrant and configured provider integrations directly, avoiding a separate LangChain model-planning request; text chat continues to use the LangChain agent. Model availability and usage depend on your provider accounts.

The LiveKit call screen includes a CrystalizedBall visual, loaded on demand with OGL from esm.sh; an internet connection is required for that optional animation. The call UI falls back to its Nova icon if WebGL2 or the animation dependency is unavailable.

## Qdrant vector knowledge base

The app stores knowledge in a persistent [Qdrant](https://qdrant.tech/) vector database. The included Docker Compose file runs Qdrant locally and persists its data in `data/qdrant/`. The Qdrant client generates embeddings with FastEmbed using `BAAI/bge-small-en`; chat and voice support share the same collection.

To use Qdrant Cloud or a different Qdrant server, set `QDRANT_URL` and `QDRANT_API_KEY` in `.env`. The local Docker server is unauthenticated by default and should only be exposed on a trusted development machine.

The included `knowledge_base/faqs.json` covers billing and payment troubleshooting, prepaid recharge and validity, and network and coverage questions. These answers provide general guidance; account-specific charges, provider policies, coverage, and live outage status must be confirmed through a carrier integration or official support. The FAQ file is ingested automatically the first time the app starts. To add source documents, put PDF, Markdown, or UTF-8 text files under `knowledge_base/` (a `knowledge_base/documents/` subfolder is fine), then rebuild the index:

```powershell
python ingest.py
```

PDFs are split into page-aware text chunks and indexed with their file and page as citation metadata. Re-running ingestion rebuilds the telecom collection from all supported knowledge-base files. **The example telecom policies are illustrative, not official carrier policies**; replace them and add verified source PDFs before using the app with customers.

## Before production

Add authentication, rate limiting, monitoring, and an approved support contact. Review privacy and retention requirements, replace demo policy content, and serve the frontend and API over HTTPS. Never expose LiveKit API secrets in the browser.
