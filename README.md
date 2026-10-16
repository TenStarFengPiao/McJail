👋 Hi, everyone! McJail is an AI-powered campus assistant for QQ, independently developed and maintained by **[TenStar](https://github.com/TenStarFengPiao)**.

<p align="center">
  <a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB.svg"></a>
  <a href="https://github.com/TenStarFengPiao/McJail/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/TenStarFengPiao/McJail/actions/workflows/ci.yml/badge.svg"></a>
  <a href="docs/TECHNICAL_DOCUMENTATION.md"><img alt="Documentation" src="https://img.shields.io/badge/Documentation-McJail-2ea44f.svg"></a>
</p>

<p align="center">
  <img alt="McJail: a hamburger, open book, and chat bubble beside the project name" src="assets/mcjail-banner-en.png" width="800">
</p>

## McJail: Your Campus Companion on QQ

McJail brings school policies, notices, documents, and reminders into a familiar conversation. Ask a question, follow the sources, or request the original file without switching between scattered announcements and folders.

Built around a **single tool-using agent**, McJail combines knowledge retrieval, file preparation, time and weather queries, and administrator tools. Message delivery and administrative confirmation are handled by application code, while SQLite stores documents, conversation history, and delivery receipts.

The goal is simple: make campus information easier to find, understand, and share, with a practical setup that one person can run and maintain.

> A friendly face for everyday campus questions, with source references and explicit approval for administrative actions.

## Highlights

- 📚 **Knowledge at hand**: retrieve policies and notices with hybrid vector and keyword search, optional reranking, and source references.
- 🍔 **Documents on request**: deliver original files and registered templates through QQ, with title, alias, and conversation-based lookup.
- 🔔 **Notifications with approval**: preview immediate, one-time, daily, or weekly notifications before explicitly confirming them.
- 📨 **Reviewed group forwarding**: collect source-group messages in fixed 60-second batches, then review and approve publication.
- 🕒 **Useful everyday tools**: query Beijing time and district-level hourly weather forecasts.
- 🧰 **Practical operations**: manage documents through a CLI or an authenticated web dashboard, with locked dependencies and deployment templates.

## Installation

### From source

McJail requires **Python 3.11+**. Install dependencies with uv:

```bash
git clone https://github.com/TenStarFengPiao/McJail.git
cd McJail
uv sync --locked --extra dev
```

Create your local configuration:

```bash
# Linux / macOS
cp .env.example .env
```

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

Configure the following values in `.env`:

| Setting | Purpose |
| --- | --- |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | Chat model credentials and endpoint |
| `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL` | Online document and query embeddings |
| `RERANK_API_KEY`, `RERANK_MODEL`, `RERANK_URL` | Optional cloud reranking |
| `NAPCAT_HTTP_URL`, `NAPCAT_WS_URL`, `NAPCAT_ACCESS_TOKEN` | NapCat HTTP and WebSocket connection |
| `MAIDANLAO_ADMIN_TOKEN` | Document dashboard authentication |
| `NOTIFICATION_ADMIN_QQ` | Authorized QQ administrator |
| `DATABASE_URL` | SQLite database location |

An empty reranking key reuses the embedding key. Set `RERANK_MODEL=` to disable cloud reranking.

The Python package remains named `maidanlao`; use that name in the commands below.

## Quick Start

### 1. Check the local retrieval pipeline

```bash
uv run python -m maidanlao.cli smoke
```

This check uses an in-memory database and an explicitly injected offline embedding implementation. It verifies the ingestion and retrieval pipeline without testing your live model or QQ connection.

### 2. Try a conversation

After configuring your chat model:

```bash
uv run python -m maidanlao.cli ask --open-id demo-user --source private --text "Hello! What can you help me with?"
```

CLI conversations return text. Request attachments through QQ to receive files.

### 3. Connect QQ

Enable the HTTP and WebSocket servers in NapCat with the same access token, then start McJail:

```bash
uv run python -m maidanlao.cli run-bot
```

Users interact with the assistant through private messages. Group chats remain silent and are collected only under confirmed forwarding rules. The notification scheduler and forwarding worker run with the bot.

### 4. Open the document dashboard

```bash
uv run python -m maidanlao.cli run-admin --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787/admin?token=YOUR_ADMIN_TOKEN`. API clients can authenticate with `Authorization: Bearer YOUR_ADMIN_TOKEN`.

To start both the bot and dashboard:

```bash
uv run python -m maidanlao.cli run-all
```

## Knowledge and Files

Import UTF-8 Markdown or text through the dashboard. Each document has a title, source, publication date, and supported category. You can attach the original PDF or Word file alongside the searchable text.

Search content and original files serve different purposes: the text is indexed for retrieval, while the original file is preserved for delivery. Originals are limited to **20 MiB** and cannot be overwritten after attachment.

Templates can be registered separately without creating embeddings:

```bash
uv run python -m maidanlao.cli import-resource --resource-id res_project_template_2027 --file project-template.docx --title "2027 Project Template" --source "Academic Office" --keyword "project template" --cohort 2027
uv run python -m maidanlao.cli list-resources
```

Inspect documents, rebuild a selected index, or run retrieval evaluations:

```bash
uv run python -m maidanlao.cli list-docs
uv run python -m maidanlao.cli reingest-doc --document-id doc_example
uv run python -m maidanlao.cli eval-rag
uv run python -m maidanlao.cli eval-rag-regression
```

Online embedding failures stop ingestion rather than silently replacing vectors. During retrieval, an available keyword channel can continue if vector search fails.

## Notifications and Forwarding

The authorized QQ administrator can query notification tasks and prepare changes through private messages. Creation, modification, natural-language cancellation, and manual retry use previews and explicit confirmation. Exact legacy cancellation commands remain supported.

For notifications, an “all” audience means the bot's QQ friends. Recipients are selected during preview and validated again at confirmation. Scheduled delivery uses the frozen recipient list and message body.

Group forwarding follows a separate review process: collect messages, deliver the batch for review, prepare a preview, and explicitly confirm the publication version. Attachments use native forwarding of their original messages.

Delivery receipts record API confirmations. An uncertain network result is kept as unknown rather than automatically resent; an API confirmation does not mean the recipient has read the message.

## How It Works

1. **Receive**: the QQ adapter normalizes private messages, removes duplicates, and serializes requests within each conversation.
2. **Prepare**: the assistant loads the latest eight complete conversation turns and builds a request-scoped agent.
3. **Use tools**: the agent selects knowledge search, file preparation, time, weather, or authorized administrative tools.
4. **Validate**: the service checks source validity and document versions, then prepares plain-text output and attachments.
5. **Deliver**: the QQ adapter sends files and text pages, records receipts, and saves the complete conversation turn after successful delivery.

Administrative confirmation and scheduled sending are application responsibilities. They are not agent tools.

## Development

Install development dependencies and run the checks:

```bash
uv sync --locked --extra dev
uv run python -m pytest --cov=src/maidanlao --cov-report=term-missing --cov-fail-under=80
uv run python -m ruff check src tests
uv run python -m ruff format --check src tests
uv lock --check
uv run python -m maidanlao.cli smoke
```

Tests use temporary databases, scripted models, and simulated network calls. CI is configured for Python 3.11, 3.12, and 3.13 on Linux and Windows.

Before upgrading a production installation, stop the services and back up SQLite, including any outstanding WAL data, plus source documents and configuration. Index rebuilding is explicit.

## Documentation

- [Technical documentation](docs/TECHNICAL_DOCUMENTATION.md)
- [Project structure](docs/PROJECT_STRUCTURE.md)
- [Engineering standards](docs/ENGINEERING_STANDARDS.md)
- [Agent request flow](docs/AGENT_REQUEST_FLOW.md)
- [File delivery](docs/FILE_DELIVERY.md)
- [Administrator notifications](docs/NOTIFICATIONS.md)
- [Group forwarding](docs/GROUP_FORWARDING.md)
- [Time and weather tools](docs/TIME_WEATHER.md)

## Author

McJail is a solo project, independently designed, developed, and maintained by **[TenStar](https://github.com/TenStarFengPiao)**.

Found a bug or have an idea? Open an [issue](https://github.com/TenStarFengPiao/McJail/issues) with the relevant steps and expected behavior.
