# EXAI Research Interview

Streamlit app for structured expert interviews using `content/research_blocks.json`, scoping agents A11–A22, and canonical questions from the JSON corpus.

## Local setup

```bash
cd EXAI_Agents
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # add OPENROUTER_API_KEY
export PYTHONPATH=.
streamlit run app.py
```

An **OPENROUTER_API_KEY** is required for the app to call the model.

## GitHub

1. Create an empty repository on GitHub (no README if you already have one locally).
2. In the project directory:

```bash
git init
git add .
git commit -m "Initial commit: Streamlit research interview app"
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

Do not commit `.env` or `.streamlit/secrets.toml` (they are listed in `.gitignore`).

## Streamlit Community Cloud

1. Sign in at [share.streamlit.io](https://share.streamlit.io), connect GitHub, **Deploy an app**.
2. **Repository** — your repo, **Main file path**: `app.py`, **Branch**: `main`.
3. **Advanced settings** → **Secrets** — paste TOML (example below), save.

### Secrets format

Uses [OpenRouter](https://openrouter.ai) (OpenAI-compatible API).

```toml
OPENROUTER_API_KEY = "sk-or-v1-..."
OPENROUTER_MODEL = "openai/gpt-4o-mini"
```

- `OPENROUTER_API_KEY` — required for live model calls (from the OpenRouter dashboard).
- `OPENROUTER_MODEL` — optional OpenRouter model id (e.g. `openai/gpt-4o-mini`, `anthropic/claude-3.5-sonnet`).
- Optional: `OPENROUTER_BASE_URL`, `OPENROUTER_HTTP_REFERER`, `OPENROUTER_X_TITLE` per [OpenRouter docs](https://openrouter.ai/docs).

During an interview, the sidebar **Agent pipeline logs** section lets you inspect each agent call (system prompt, user payload, raw response, parsed JSON where applicable) and **download** them as JSON.

After saving secrets, **Reboot app** if it was already running.

## Tests

```bash
PYTHONPATH=. pytest tests/ -q
```
