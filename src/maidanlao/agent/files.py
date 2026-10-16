"""只读文件准备工具：模型判断意图，服务按本轮名称或明确指代约束文档 ID。"""

import json
import logging
import re
from dataclasses import dataclass, field

from langchain_core.tools import tool

from maidanlao.rag.models import OriginalAttachment
from maidanlao.rag.originals import validate_original

logger = logging.getLogger(__name__)


def file_request_denied(content: str) -> bool:
    """否定与矛盾请求采用保守策略；仅影响附件，不阻止普通政策回答。"""
    return bool(
        re.search(r"(?:不要|不用|无需|别|不需要|不想).{0,16}(?:发|给|下载|文件|原件|原文|模板|附件|资料)", content)
    )


def is_reference_request(content: str) -> bool:
    """仅允许纯指代请求复用历史；留下具体名称的请求不得回退。"""
    remaining = re.sub(
        r"原始文件|原文件|原文档|原件|文件|模板|资料|附件|再发一次|再发|刚才|上次|上面|这份|那份|这个|那个"
        r"|发给我|给我|提供|下载|获取|索要|发送|发|要|请|帮我|一下|一份|把|将|的|吧|呀|哦|给|规定|文档|能不能|可以|能|吗",
        "",
        content,
    )
    return not re.sub(r"[\s，。！？,.!?～~]", "", remaining)


@dataclass
class FileSession:
    """请求内附件清单；二进制只留在服务端，不进入模型上下文。"""

    repos: object
    turns: list
    content: str
    search: object
    attachments: dict[str, OriginalAttachment] = field(default_factory=dict)
    sources: dict[str, dict] = field(default_factory=dict)
    attempted: bool = False
    notice: str = ""

    def catalog(self) -> list[dict]:
        """提供有限的定位元数据；不暴露文件路径、字节或数据库对象。"""
        result = []
        for doc in self.repos.knowledge_documents.list_all():
            if doc["status"] == "active":
                result.append(
                    {
                        "id": doc["id"],
                        "title": doc["title"],
                        "aliases": json.loads(doc["aliases_json"]),
                        "cohort": next(iter(re.findall(r"(20\d{2})届", doc["title"] + doc["scope"])), ""),
                    }
                )
        for resource in self.repos.resources.list_all():
            if resource["status"] == "active":
                result.append(
                    {
                        "id": resource["id"],
                        "title": resource["title"],
                        "aliases": [
                            *json.loads(resource["keywords_json"]),
                            resource["filename"],
                            re.sub(r"\.(?:pdf|docx?|doc)$", "", resource["filename"], flags=re.I),
                        ],
                        "cohort": resource["cohort"],
                    }
                )
        return result

    def candidates(self) -> tuple[set[str], list[dict]]:
        """显式名称优先；只有纯指代请求才加载同会话最近来源。"""
        catalog = self.catalog()
        titles = re.findall(r"《([^》]+)》", self.content)
        explicit_ids = re.findall(r"\b(?:doc|res)_[A-Za-z0-9_-]+\b", self.content)
        named = set()
        for item in catalog:
            names = [item["title"], *item["aliases"]]
            if titles or explicit_ids:
                matched = item["id"] in explicit_ids or any(
                    re.sub(r"\.(?:pdf|docx?|md|txt)$", "", title, flags=re.I) in names for title in titles
                )
            else:
                matched = item["id"] in self.content or any(name in self.content for name in names)
            cohorts = re.findall(r"(20\d{2})\s*届", self.content)
            if matched and not (cohorts and item.get("cohort") and item["cohort"] not in cohorts):
                named.add(item["id"])
        # 任何显式名称未知时都失败，不能拿历史或另一个已知名称顶替。
        if titles and any(
            not any(
                re.sub(r"\.(?:pdf|docx?|md|txt)$", "", title, flags=re.I) in [i["title"], *i["aliases"]]
                for i in catalog
            )
            for title in titles
        ):
            return set(), []
        if explicit_ids and any(i not in {c["id"] for c in catalog} for i in explicit_ids):
            return set(), []
        recent = next(
            (json.loads(turn.sources_json) for turn in reversed(self.turns) if json.loads(turn.sources_json)), []
        )
        if named:
            return named, [
                source for source in recent if (source.get("document_id") or source.get("resource_id")) in named
            ]
        if titles or explicit_ids or not is_reference_request(self.content):
            return set(), []
        return {s.get("document_id") or s.get("resource_id") for s in recent} - {None}, recent

    def get(self, document_id: str) -> tuple[str, dict]:
        """只准备唯一且可验证的文件；任何一次工具失败都清空待发附件。"""
        self.attempted = True
        if file_request_denied(self.content):
            return self.reject("本轮包含不发送文件的要求或矛盾表达，我不会发送附件；如果需要，请明确说明。")
        ids, references = self.candidates()
        if not ids:
            candidates = {s["document_id"]: s["source_title"] for s in self.search.sources.values()}
            options = "\n".join(f"《{title}》（{doc_id}）" for doc_id, title in candidates.items())
            return self.reject(
                "没有找到明确指定的文件，请提供完整标题或登记的简称。" + ("\n可选文档：\n" + options if options else "")
            )
        explicit_many = len(re.findall(r"《([^》]+)》", self.content)) > 1 and not re.search(
            r"还是|或者|任选|或", self.content
        )
        if len(ids) != 1 and not explicit_many:
            titles = [f"《{item['title']}》" for item in self.catalog() if item["id"] in ids]
            return self.reject("涉及多份文档，请明确要哪一份原文件：\n" + "\n".join(titles))
        if document_id not in ids:
            return self.reject("所选文件与本轮指定文档不一致，请提供准确标题；不会发送历史中的其他文件。")
        doc = self.repos.knowledge_documents.get(document_id)
        resource = self.repos.resources.get(document_id) if doc is None else None
        item = doc or resource
        if item is None or item.status != "active":
            return self.reject("该引用文档已不可用，请重新查询。")
        version_key = "document_version" if doc else "resource_version"
        if any(s.get(version_key) not in (None, "", item.version) for s in references):
            return self.reject("上次引用的文档版本已变化，请重新查询后再索要原文件。")
        original = self.repos.knowledge_documents.original(document_id) if doc else resource
        if original is None:
            return self.reject(f"《{item.title}》尚未关联学校原始文件，暂时无法提供原件。")
        try:
            if validate_original(original.filename, original.content) != original.sha256:
                raise ValueError("integrity_failed")
        except ValueError:
            return self.reject("原文件完整性校验失败，暂时无法提供，请联系管理员。")
        self.notice = ""
        self.attachments[document_id] = OriginalAttachment(document_id, original.filename, original.content)
        source = {
            "document_id" if doc else "resource_id": document_id,
            version_key: item.version,
            "source_title": item.title,
        }
        self.sources[document_id] = source
        return f"已准备《{item.title}》的原文件，QQ 适配层将尝试发送。不要声称用户已经收到。", {
            "sources": [source],
            "filename": original.filename,
        }

    def reject(self, message: str) -> tuple[str, dict]:
        """保留可信失败原因，覆盖模型可能生成的交付成功措辞。"""
        self.attachments.clear()
        self.sources.clear()
        self.notice = message
        return message, {"sources": []}


def build_file_tool(files: FileSession):
    """声明只读工具；没有 QQ 收件人参数，也不会发消息。"""

    @tool(response_format="content_and_artifact")
    def get_document_file(document_id: str) -> tuple[str, dict]:
        """用户明确索要文档原件或模板时，按目录中的 ID 准备文件。歧义时先问，不要猜测。"""
        return files.get(document_id)

    return get_document_file
