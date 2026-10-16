"""知识库后台：仅暴露文档管理，不提供聊天或业务写操作。"""

import json
import logging
import os
import re
from contextlib import closing
from datetime import date
from email.parser import BytesParser
from email.policy import default as email_policy
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from maidanlao.api.auth import authorized
from maidanlao.api.routes.health import health_response
from maidanlao.config.settings import database_path
from maidanlao.llm.client import LangChainEmbeddingClient
from maidanlao.persistence.database import open_database
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag._shared import CATEGORY_MAP
from maidanlao.rag.ingestion import KnowledgeIngestService
from maidanlao.rag.originals import MAX_ORIGINAL_BYTES, validate_original

logger = logging.getLogger(__name__)


def parse_fields(raw: bytes, content_type: str) -> dict:
    """解析检索正文和可选原件；原件字节只存储，不作为正文解码。"""
    if content_type.startswith("application/json"):
        fields = json.loads(raw.decode("utf-8"))
        if not isinstance(fields, dict):
            raise ValueError("invalid_import")
    elif content_type.startswith("multipart/form-data"):
        message = BytesParser(policy=email_policy).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw
        )
        fields = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                if name == "original_file":
                    validate_original(filename, payload)
                    fields["original_name"] = filename
                    fields["original_content"] = payload
                    continue
                if not filename.lower().endswith((".md", ".txt")):
                    raise ValueError("only_utf8_txt_md")
                name = "raw_text"
            fields[name] = payload.decode("utf-8-sig")
    else:
        fields = {key: values[-1] for key, values in parse_qs(raw.decode("utf-8")).items()}
    return fields


def parse_import(raw: bytes, content_type: str) -> dict:
    """校验导入元数据，将语料和原件交给同一入库事务。"""
    fields = parse_fields(raw, content_type)
    result = {}
    for name in ("title", "source", "publish_date", "category", "raw_text"):
        value = fields.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("missing_" + name)
        result[name] = value
    date.fromisoformat(result["publish_date"])
    result["scope"] = str(fields.get("scope") or "school")
    result["created_by"] = 0
    if fields.get("document_id"):
        document_id = fields["document_id"]
        if not isinstance(document_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", document_id):
            raise ValueError("invalid_document_id")
        result["document_id"] = document_id
    if "original_name" in fields or "original_content" in fields:
        if "original_name" not in fields or "original_content" not in fields:
            raise ValueError("incomplete_original")
        validate_original(fields.get("original_name"), fields["original_content"])
        result.update(original_name=fields["original_name"], original_content=fields["original_content"])
    return result


def render_dashboard(documents: list[dict]) -> str:
    """转义文档元数据；页面使用同一 token URL 发起文档操作。"""
    rows = "".join(
        "<tr><td>"
        + escape(str(doc["id"]))
        + "</td><td>"
        + escape(doc["title"])
        + "</td><td>"
        + escape(doc["source"])
        + "</td><td>"
        + (
            "<a href='/admin/documents/" + quote(str(doc["id"]), safe="") + "/original'>下载原件</a>"
            if doc.get("has_original")
            else "<input type='file' accept='.pdf,.docx,.doc' data-original='"
            + escape(str(doc["id"]), quote=True)
            + "'>"
        )
        + "</td><td><button data-id='"
        + escape(str(doc["id"]), quote=True)
        + "'>重建索引</button></td></tr>"
        for doc in documents
    )
    options = "".join(f"<option>{escape(category)}</option>" for category in CATEGORY_MAP)
    return (
        """<!doctype html><html lang="zh"><meta charset="utf-8"><title>知识库管理</title>
    <style>body{font:16px sans-serif;max-width:960px;margin:40px auto}td{padding:12px}
    input,textarea{display:block;margin:12px 0;width:95%;padding:8px}button{padding:8px}</style>
    <h1>知识文档</h1><table>"""
        + rows
        + """</table><h2>导入 UTF-8 文本 / Markdown</h2>
    <form id="import"><input name="title" placeholder="标题" required>
    <input name="document_id" placeholder="文档 ID（可选，留空自动生成）">
    <input name="source" placeholder="来源" required><input name="publish_date" type="date" required>
    <select name="category" required>"""
        + options
        + """</select>
    <input name="scope" value="school"><textarea name="raw_text" placeholder="清洗后的检索正文"></textarea>
    <label>检索语料<input name="file" type="file" accept=".txt,.md"></label>
    <label>学校原始文件（可选，最大20MB）<input name="original_file" type="file" accept=".pdf,.docx,.doc"></label>
    <button>导入并建立索引</button></form><pre id="result"></pre>
    <script>
    async function post(path, body) {
        const r = await fetch(path + location.search, {method:'POST', body});
        document.querySelector('#result').textContent = JSON.stringify(await r.json(), null, 2);
        if(r.ok) location.reload();
    }
    document.querySelector('#import').onsubmit = e => {e.preventDefault();
        const data = new FormData(e.target); if(!data.get('file').size) data.delete('file');
        if(!data.get('original_file').size) data.delete('original_file');
        post('/admin/documents/import', data);};
    document.querySelectorAll('a').forEach(a => a.href += location.search);
    document.querySelectorAll('[data-original]').forEach(input => input.onchange = () => {
        if(!input.files.length) return;
        const data = new FormData(); data.append('original_file', input.files[0]);
        post('/admin/documents/' + encodeURIComponent(input.dataset.original) + '/original', data);
    });
    document.querySelectorAll('[data-id]').forEach(b => b.onclick = () =>
        post('/admin/documents/' + encodeURIComponent(b.dataset.id) + '/reindex'));
    </script></html>"""
    )


def create_admin_server(host="127.0.0.1", port=8787, db_path=None, token=None, embedding_client=None):
    """创建同步导入后台；每个请求独立连接，索引失败回滚保留旧版本。"""
    selected_database = database_path(db_path)
    admin_token = token if token is not None else os.getenv("MAIDANLAO_ADMIN_TOKEN", "")
    embedder = embedding_client if embedding_client is not None else LangChainEmbeddingClient()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            """先判断路由，再鉴权；退出的旧接口始终返回 404。"""
            route = urlparse(self.path).path
            original_match = re.fullmatch(r"/admin/documents/([^/]+)/original", route)
            if route == "/health":
                status, payload = health_response(admin_token)
                self._send(status, payload)
                return
            if route not in {"/admin", "/admin/documents"} and not original_match:
                self._send(404, {"error": "not_found"})
                return
            if not self._auth():
                return
            with closing(open_database(selected_database)) as db:
                repo = KnowledgeRepository(db).knowledge_documents
                if original_match:
                    original = repo.original(unquote(original_match.group(1)))
                    if original is None:
                        self._send(404, {"error": "original_not_found"})
                        return
                    try:
                        digest = validate_original(original.filename, original.content)
                    except ValueError:
                        digest = None
                    if digest != original.sha256:
                        self._send(409, {"error": "original_integrity_failed"})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(original.content)))
                    self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(original.filename))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(original.content)
                    return
                docs = repo.list_all()
                if route == "/admin":
                    self._send(200, render_dashboard(docs), html=True)
                else:
                    self._send(200, {"documents": docs})

        def do_POST(self):  # noqa: N802
            """同步导入或指定文档重建，任何失败均不提交部分索引。"""
            route = urlparse(self.path).path
            match = re.fullmatch(r"/admin/documents/([^/]+)/reindex", route)
            original_match = re.fullmatch(r"/admin/documents/([^/]+)/original", route)
            if route != "/admin/documents/import" and not match and not original_match:
                self._discard_body()
                self._send(404, {"error": "not_found"})
                return
            if not self._auth():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                multipart = self.headers.get("Content-Type", "").startswith("multipart/form-data")
                max_size = MAX_ORIGINAL_BYTES + 2 * 1024 * 1024 if multipart else 2 * 1024 * 1024
                if length < 0 or length > max_size:
                    self._send(413, {"error": "request_too_large"})
                    return
                raw = self.rfile.read(length)
                with closing(open_database(selected_database)) as db:
                    repos = KnowledgeRepository(db)
                    service = KnowledgeIngestService(repos, embedder)
                    if match or original_match:
                        doc_id = unquote((match or original_match).group(1))
                        if repos.knowledge_documents.get(doc_id) is None:
                            self._send(404, {"error": "document_not_found"})
                            return
                        if original_match:
                            fields = parse_fields(raw, self.headers.get("Content-Type", ""))
                            repos.knowledge_documents.attach_original(
                                doc_id, fields.get("original_name"), fields.get("original_content")
                            )
                        else:
                            doc_id = service.reingest_document(doc_id)
                    else:
                        fields = parse_import(raw, self.headers.get("Content-Type", ""))
                        doc_id = service.ingest_text(**fields)
                self._send(200 if match or original_match else 201, {"document_id": doc_id})
            except (ValueError, UnicodeError) as exc:
                self._send(400, {"error": str(exc)})
            except Exception as exc:
                logger.warning("document_index_failed type=%s", type(exc).__name__)
                self._send(503, {"error": "document_index_failed"})

        def _auth(self):
            if authorized(admin_token, self.headers.get("Authorization", ""), self.path):
                return True
            if self.command == "POST":
                self._discard_body()
            self._send(401, {"error": "admin_token_required"})
            return False

        def _discard_body(self):
            """退出路由也消费小请求体，避免 Windows 关闭连接时丢失响应。"""
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if 0 < length <= 2 * 1024 * 1024:
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass

        def _send(self, status, payload, html=False):
            body = (payload if html else json.dumps(payload, ensure_ascii=False)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            """避免访问日志泄露 query token。"""

    return ThreadingHTTPServer((host, port), Handler)


def run_admin_server(host="127.0.0.1", port=8787, db_path=None, token=None):
    """阻塞运行后台，退出时释放端口。"""
    with create_admin_server(host, port, db_path, token) as server:
        server.serve_forever()
