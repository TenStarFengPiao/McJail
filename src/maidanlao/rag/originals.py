"""原件大小、文件名与完整性校验；不从原件抽取检索文本。"""

import hashlib

MAX_ORIGINAL_BYTES = 20 * 1024 * 1024


def validate_original(filename: str, content: bytes) -> str:
    """限制附件大小和文件名，返回原始字节的 SHA256。"""
    if (
        not isinstance(filename, str)
        or not filename.strip()
        or any(c in filename for c in "/\\\x00\r\n")
        or filename in {".", ".."}
        or not filename.lower().endswith((".pdf", ".docx", ".doc"))
    ):
        raise ValueError("invalid_original_filename")
    if not isinstance(content, bytes) or not content or len(content) > MAX_ORIGINAL_BYTES:
        raise ValueError("invalid_original_size")
    return hashlib.sha256(content).hexdigest()
