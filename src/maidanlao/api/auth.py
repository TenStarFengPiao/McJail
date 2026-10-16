"""后台 Bearer/query token 解析；认证规则不依赖业务服务。"""

import hmac
from urllib.parse import parse_qs, urlparse


def request_token(path: str) -> str:
    """读取后台页面链接中的 token；没有 token 时返回空字符串。"""
    return parse_qs(urlparse(path).query).get("token", [""])[0]


def authorized(expected: str, bearer: str, path: str) -> bool:
    """使用固定时间比较校验 token；未配置服务 token 时拒绝访问。"""
    supplied = bearer.removeprefix("Bearer ") if bearer.startswith("Bearer ") else request_token(path)
    return bool(expected and supplied) and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
