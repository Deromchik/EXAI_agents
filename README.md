# EXAI Research Interview

Streamlit-інтерв’ю з блоками з `content/research_blocks.json`, scoping-агенти A11–A22 і канонічні питання з корпусу.

## Локально

```bash
cd EXAI_Agents
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # додайте OPENROUTER_API_KEY
export PYTHONPATH=.
streamlit run app.py
```

Або без API: у сайдбарі увімкніть **Mock LLM** або `EXAI_MOCK_LLM=1`.

## GitHub

1. Створіть порожній репозиторій на GitHub (без README, якщо вже є локально).
2. У каталозі проєкту:

```bash
git init
git add .
git commit -m "Initial commit: Streamlit research interview app"
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

Переконайтеся, що **не** потрапили у коміт: `.env`, `.streamlit/secrets.toml` (вони в `.gitignore`).

## Streamlit Community Cloud

1. Увійдіть на [share.streamlit.io](https://share.streamlit.io), підключіть GitHub, **Deploy an app**.
2. **Repository** — ваш репозиторій, **Main file path**: `app.py`, **Branch**: `main`.
3. **Advanced settings** → **Secrets** — вставте TOML (приклад нижче) і збережіть.

### Формат Secrets

Використовується [OpenRouter](https://openrouter.ai) (OpenAI-сумісний API).

```toml
OPENROUTER_API_KEY = "sk-or-v1-..."
OPENROUTER_MODEL = "openai/gpt-4o-mini"
EXAI_MOCK_LLM = "0"
```

- `OPENROUTER_API_KEY` — обов’язково для реальних викликів (ключ з кабінету OpenRouter).
- `OPENROUTER_MODEL` — ідентифікатор моделі на OpenRouter (наприклад `openai/gpt-4o-mini`, `anthropic/claude-3.5-sonnet`).
- Опційно: `OPENROUTER_BASE_URL`, `OPENROUTER_HTTP_REFERER`, `OPENROUTER_X_TITLE` (атрибуція за [документацією OpenRouter](https://openrouter.ai/docs)).
- `EXAI_MOCK_LLM` — опційно; `"1"` увімкне заглушки без API (для тесту деплою).

Після збереження secrets натисніть **Reboot app**, якщо додаток уже був запущений.

## Тести

```bash
PYTHONPATH=. pytest tests/ -q
```
