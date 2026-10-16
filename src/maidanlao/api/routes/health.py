"""存活探针；只报告后台认证配置，不宣称外部依赖可用。"""


def health_response(token: str) -> tuple[int, dict[str, str]]:
    """返回 HTTP 状态与 JSON，未配置管理 token 时为 503。"""
    return (200, {"status": "ok"}) if token else (503, {"status": "unconfigured", "reason": "admin_token_missing"})
