"""
Token 鉴权中间件与依赖
- SECRET_TOKEN 从环境变量读取
- 写操作（POST/PATCH/DELETE）强制 Bearer Token
- GET 可通过 READONLY_PASSWORD 配置简单密码（可选）
"""

import os
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SECRET_TOKEN = os.getenv("SECRET_TOKEN", "").strip()
READONLY_PASSWORD = os.getenv("READONLY_PASSWORD", "").strip()
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").strip()

_bearer_scheme = HTTPBearer(auto_error=False)


def require_write_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> None:
    """
    写操作鉴权依赖。
    若未配置 SECRET_TOKEN 则拒绝所有写操作（避免生产环境裸奔）。
    """
    if not SECRET_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务端未配置 SECRET_TOKEN，写操作已禁用",
        )
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Authorization: Bearer <token> 请求头",
        )
    if not secrets.compare_digest(credentials.credentials, SECRET_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="鉴权失败：Token 无效",
        )


def require_read_access(
    authorization: Optional[str] = Header(None),
) -> None:
    """
    读操作鉴权依赖。
    - 若未配置 READONLY_PASSWORD 则公开访问
    - 若配置了则需要 Header: Authorization: Bearer <READONLY_PASSWORD>
      或同样接受 SECRET_TOKEN（管理员可读）
    """
    if not READONLY_PASSWORD:
        return
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="该接口需要鉴权，请在 Header 中携带 Authorization: Bearer <密码>",
        )
    token = ""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
    else:
        token = authorization.strip()
    accepted = [READONLY_PASSWORD]
    if SECRET_TOKEN:
        accepted.append(SECRET_TOKEN)
    for t in accepted:
        if secrets.compare_digest(token, t):
            return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="读访问鉴权失败",
    )
