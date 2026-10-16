"""CLI 与进程启动入口；所有问答均使用同一 Agent 服务。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path

from maidanlao.config.settings import database_path
from maidanlao.llm.client import LangChainEmbeddingClient
from maidanlao.persistence.database import open_database
from maidanlao.persistence.repository import KnowledgeRepository
from maidanlao.rag import HybridRetriever, KnowledgeIngestService, RagEvaluationService
from maidanlao.rag.embeddings import LocalHashEmbeddingClient
from maidanlao.service import AssistantService


def run_smoke() -> dict:
    """显式注入离线 Hash 向量，在临时内存库检查完整入库检索链路。"""
    with closing(open_database(":memory:")) as db:
        repos = KnowledgeRepository(db)
        embedder = LocalHashEmbeddingClient()
        KnowledgeIngestService(repos, embedder).ingest_text(
            title="补考通知",
            source="学院教学办",
            publish_date="2026-09-10",
            scope="school",
            category="补考通知",
            raw_text="# 补考通知\n\n补考报名截止到周五，考试地点见教务系统。",
            created_by=0,
        )
        result = HybridRetriever(repos, embedder).search("补考报名什么时候截止")
        return {
            "status": "ok" if result.evidence_chunks else "failed",
            "rag_status": result.status,
            "evidence_count": len(result.evidence_chunks),
            "context": result.final_context,
        }


def _start_child(args: list[str]) -> subprocess.Popen:
    """继承当前解释器与环境，启动受父进程管理的组件。"""
    return subprocess.Popen(
        [sys.executable, "-m", "maidanlao.cli", *args],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def run_all(admin_host="127.0.0.1", admin_port=8787) -> int:
    """仅启动后台与 QQ；任一子进程退出后停止另一个，Ctrl+C 清理两者。"""
    children = []
    stop = threading.Event()
    try:
        children.append(_start_child(["run-admin", "--host", admin_host, "--port", str(admin_port)]))
        children.append(_start_child(["run-bot"]))
        while not stop.wait(1):
            for proc in children:
                if proc.poll() is not None:
                    return proc.returncode or 1
    except KeyboardInterrupt:
        return 0
    finally:
        for proc in children:
            if proc.poll() is None:
                proc.terminate()
        for proc in children:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def main(argv: list[str] | None = None) -> int:
    """解析保留的运维命令；失败返回非零状态，不输出密钥与模型响应详情。"""
    parser = argparse.ArgumentParser(prog="maidanlao")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("smoke")
    ask = sub.add_parser("ask")
    ask.add_argument("--open-id", required=True)
    ask.add_argument("--group-id")
    ask.add_argument("--source", choices=["private", "group"], required=True)
    ask.add_argument("--text", required=True)
    sub.add_parser("run-bot")
    for name in ("run-admin", "run-all"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--host", default="127.0.0.1")
        cmd.add_argument("--port", type=int, default=8787)
    sub.add_parser("eval-rag")
    sub.add_parser("eval-rag-regression")
    sub.add_parser("list-docs")
    aliases = sub.add_parser("set-doc-aliases")
    aliases.add_argument("--document-id", required=True)
    aliases.add_argument("--alias", action="append", required=True)
    reindex = sub.add_parser("reingest-doc")
    reindex.add_argument("--document-id", required=True)
    importer = sub.add_parser("import-doc")
    for name in ("file", "title", "source", "publish-date", "category"):
        importer.add_argument("--" + name, required=True)
    importer.add_argument("--scope", default="school")
    importer.add_argument("--document-id")
    importer.add_argument("--original-file", help="关联学校原始 PDF 或 Word 文件，不用于检索")
    original_cmd = sub.add_parser("attach-original")
    original_cmd.add_argument("--document-id", required=True)
    original_cmd.add_argument("--file", required=True)
    sub.add_parser("list-resources")
    resource_cmd = sub.add_parser("import-resource")
    for name in ("resource-id", "file", "title", "source"):
        resource_cmd.add_argument("--" + name, required=True)
    resource_cmd.add_argument("--keyword", action="append", required=True)
    resource_cmd.add_argument("--description", default="")
    resource_cmd.add_argument("--cohort", default="")
    resource_cmd.add_argument("--only-on-request", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "smoke":
            result = run_smoke()
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] == "ok" else 1
        if args.command == "ask":
            print(AssistantService().handle_text(args.open_id, args.group_id, args.source, args.text))
            return 0
        if args.command == "run-all":
            return run_all(args.host, args.port)
        if args.command == "run-bot":
            from maidanlao.adapters.napcat import NapCatBot

            NapCatBot().run()
            return 0
        if args.command == "run-admin":
            from maidanlao.api.server import run_admin_server

            run_admin_server(args.host, args.port)
            return 0
        with closing(open_database(database_path())) as db:
            repos = KnowledgeRepository(db)
            if args.command == "list-docs":
                result = repos.knowledge_documents.list_all()
            elif args.command == "set-doc-aliases":
                repos.knowledge_documents.set_aliases(args.document_id, args.alias)
                result = {"document_id": args.document_id, "aliases": args.alias}
            elif args.command == "list-resources":
                result = repos.resources.list_all()
            elif args.command == "import-resource":
                from maidanlao.rag.originals import MAX_ORIGINAL_BYTES

                resource_file = Path(args.file)
                if resource_file.stat().st_size > MAX_ORIGINAL_BYTES:
                    raise ValueError("invalid_original_size")
                result = {
                    "resource_id": repos.resources.register(
                        args.resource_id,
                        args.title,
                        args.source,
                        resource_file.name,
                        resource_file.read_bytes(),
                        args.keyword,
                        args.description,
                        args.cohort,
                        not args.only_on_request,
                    )
                }
            elif args.command == "attach-original":
                from maidanlao.rag.originals import MAX_ORIGINAL_BYTES

                original = Path(args.file)
                if original.stat().st_size > MAX_ORIGINAL_BYTES:
                    raise ValueError("invalid_original_size")
                repos.knowledge_documents.attach_original(args.document_id, original.name, original.read_bytes())
                result = {"document_id": args.document_id}
            elif args.command == "import-doc":
                path = Path(args.file)
                if path.suffix.lower() not in {".txt", ".md"}:
                    raise ValueError("only_utf8_txt_md")
                from maidanlao.api.server import parse_import

                fields = parse_import(
                    json.dumps(
                        {
                            "title": args.title,
                            "source": args.source,
                            "publish_date": args.publish_date,
                            "category": args.category,
                            "scope": args.scope,
                            "document_id": args.document_id,
                            "raw_text": path.read_text(encoding="utf-8-sig"),
                        }
                    ).encode(),
                    "application/json",
                )
                if args.original_file:
                    original = Path(args.original_file)
                    from maidanlao.rag.originals import MAX_ORIGINAL_BYTES

                    if original.stat().st_size > MAX_ORIGINAL_BYTES:
                        raise ValueError("invalid_original_size")
                    fields.update(original_name=original.name, original_content=original.read_bytes())
                result = {
                    "document_id": KnowledgeIngestService(repos, LangChainEmbeddingClient()).ingest_text(**fields)
                }
            elif args.command == "reingest-doc":
                result = {
                    "document_id": KnowledgeIngestService(repos, LangChainEmbeddingClient()).reingest_document(
                        args.document_id
                    )
                }
            else:
                retriever = HybridRetriever(repos, LangChainEmbeddingClient())
                if args.command == "eval-rag":
                    result = RagEvaluationService(repos, retriever).run()
                else:
                    from maidanlao.rag.regression import RagRegressionService

                    result = RagRegressionService(repos, retriever).run()
            print(json.dumps(result, ensure_ascii=False))
            return (
                1
                if isinstance(result, dict) and (result.get("passed") is False or result.get("all_passed") is False)
                else 0
            )
    except (Exception, KeyboardInterrupt) as exc:
        print("命令未完成：" + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
