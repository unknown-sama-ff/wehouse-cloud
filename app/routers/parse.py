"""
云端 LLM 解析路由（可选功能）
- 本地端如果配置了 use_cloud_parser=true，会通过此接口把 raw_text 发上来解析
- 避免把 Moonshot API Key 保存在本地电脑
- Moonshot 参数从环境变量读取：LLM_API_URL / LLM_API_KEY / LLM_MODEL

【容错原则】
- /api/parse 接口永远不会 502 / 500（除了鉴权失败 401 / 参数校验失败 422）
- LLM_API_KEY 是占位符 / 调 Moonshot 连续 3 次失败 → 自动降级到本地正则 _mock_parse()
- 正则也失败的极端情况 → 返回空字段（area=None…）但 HTTP 200
"""

import json
import logging
import os
import re
import time
from typing import Any, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.routers.auth import require_write_token

router = APIRouter(prefix="/api", tags=["parse"])

logger = logging.getLogger(__name__)

LLM_API_URL = (os.getenv("LLM_API_URL") or "https://api.moonshot.cn/v1/chat/completions").strip()
LLM_API_KEY = (os.getenv("LLM_API_KEY") or "").strip()
LLM_MODEL = (os.getenv("LLM_MODEL") or "kimi-latest").strip()

# 占位符判断：出现这些都认为是没填真 Key，直接降级正则，不请求外网（避免请求失败浪费时间+502）
_PLACEHOLDER_MARKERS = (
    "",
    "sk-xxx",
    "sk-xxxx",
    "sk-xxxxx",
    "sk-placeholder",
    "sk-your",
    "your-",
    "your-key",
    "your_api_key",
    "xxx",
    "xxxx",
    "xxxxx",
    "test",
    "demo",
    "none",
    "null",
)


def _is_placeholder_key(k: str) -> bool:
    if not k:
        return True
    low = k.strip().lower()
    for p in _PLACEHOLDER_MARKERS:
        if low == p.lower() or low.startswith(p.lower()):
            return True
    return False


SYSTEM_PROMPT = (
    "你是一个房产信息提取助手。"
    "请从用户提供的房产消息中提取结构化信息，以 JSON 格式返回。"
    "只返回 JSON 对象，不要添加任何额外说明文字或 Markdown 标记。"
)

USER_PROMPT_TEMPLATE = """\
请从以下房产消息中提取信息，返回 JSON：
{{
  "小区": "小区名称，没有则null",
  "面积": "数字+单位，没有则null",
  "总价": "数字+万，没有则null",
  "户型": "如三室两厅，没有则null",
  "楼层": "如15/28，没有则null",
  "联系人": "姓名和电话，没有则null"
}}

消息内容：{message_text}
"""


class ParseRequest(BaseModel):
    raw_text: str = Field(..., min_length=1, description="待解析的原始房产消息文本")


class ParseResponse(BaseModel):
    area: Optional[str] = None
    size: Optional[str] = None
    price: Optional[str] = None
    layout: Optional[str] = None
    floor: Optional[str] = None
    contact: Optional[str] = None


def _mock_parse(message_text: str) -> ParseResponse:
    """和本地端一致的离线正则解析，作为无 Key / LLM 失败时兜底"""
    text = message_text.strip()

    def find(patterns):
        for p in patterns:
            m = re.search(p, text)
            if m:
                v = m.group(1).strip("，,。.;；:：\t ")
                if v:
                    return v
        return None

    area = find([
        r"([\u4e00-\u9fa5A-Za-z0-9]{2,20}(?:城|花园|华府|家园|里|湾|苑|府|公寓|小区|公馆))",
        r"小区[:：]\s*([\u4e00-\u9fa5A-Za-z0-9]{2,20})",
    ])
    size = find([
        r"(\d+(?:\.\d+)?\s*(?:平|平米|平方米|㎡|m2))",
        r"(?:建筑面积|面积)[:：]?\s*(\d+(?:\.\d+)?\s*(?:平|平米|平方米|㎡|m2)?)",
    ])
    price = find([
        r"((?:总价|售价)?\s*\d+(?:\.\d+)?\s*万)",
        r"(?:总价|售价)[:：]\s*(\d+(?:\.\d+)?\s*万?)",
    ])
    if price and not price.endswith("万"):
        price = price + "万"
    layout = find([
        r"([一二三四五六七八九十两213456]室[一二三四五六七八九十两12345]厅(?:[一二三四五六七八九十两12345]卫)?)",
        r"(一居室|二居室|三居室|四居室|两居室)",
    ])
    floor = find([
        r"(\d+\s*/\s*\d+\s*层?)",
        r"(?:楼层|层)[:：]\s*(\d+\s*/?\s*\d*\s*层?)",
    ])
    phone_match = re.search(r"(1[3-9]\d{9})", text)
    contact_parts = []
    name = find([
        r"联系人[：:]\s*([\u4e00-\u9fa5A-Za-z]{2,6})",
        r"(?:联系人|联系电话|电话|看房|对接)[:：]?\s*([\u4e00-\u9fa5A-Za-z]{2,6})(?:\s*1[3-9]\d{9})",
    ])
    if name:
        contact_parts.append(name)
    if phone_match:
        contact_parts.append(phone_match.group(1))
    contact = " ".join(contact_parts) if contact_parts else None
    return ParseResponse(
        area=area, size=size, price=price, layout=layout, floor=floor, contact=contact,
    )


def _extract_json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = text[start: end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError as e:
            raise ValueError(f"无法解析模型输出为 JSON: {e}, 片段: {snippet[:200]}")
    raise ValueError(f"模型输出中未找到 JSON 对象: {text[:200]}")


def _from_llm_json(data: dict[str, Any]) -> ParseResponse:
    key_map = [
        (("小区", "area"), "area"),
        (("面积", "size"), "size"),
        (("总价", "price"), "price"),
        (("户型", "layout"), "layout"),
        (("楼层", "floor"), "floor"),
        (("联系人", "contact"), "contact"),
    ]
    kwargs: dict[str, Optional[str]] = {}
    for src_keys, dst in key_map:
        val: Optional[str] = None
        for k in src_keys:
            if k in data and data[k] is not None:
                v = data[k]
                if isinstance(v, str):
                    v = v.strip() or None
                else:
                    v = str(v).strip() or None
                val = v
                break
        kwargs[dst] = val
    return ParseResponse(**kwargs)


def _call_llm(message_text: str) -> ParseResponse:
    """
    调用 Moonshot 并做 3 次指数退避重试
    ⚠️  永远不抛异常！失败一律降级到 _mock_parse()，保证接口 HTTP 200
    """
    # 占位符 Key → 不请求外网，直接正则
    if _is_placeholder_key(LLM_API_KEY):
        logger.warning(
            "LLM_API_KEY 看起来是占位符(长度=%d, 前缀=%s)，直接降级本地正则解析",
            len(LLM_API_KEY),
            (LLM_API_KEY[:10] + "...") if len(LLM_API_KEY) > 10 else LLM_API_KEY,
        )
        return _mock_parse(message_text)

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(message_text=message_text)},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    for attempt in range(1, 4):
        try:
            resp = requests.post(
                LLM_API_URL,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=45,
            )
            # 401/403 = Key 无效 / 欠费 → 直接降级不再重试
            if resp.status_code in (401, 403):
                logger.warning(
                    "LLM 返回 %s，认为凭据无效，不再重试，降级到本地正则。响应片段: %s",
                    resp.status_code,
                    resp.text[:150],
                )
                return _mock_parse(message_text)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise ValueError("LLM 响应缺少 choices")
            content = choices[0].get("message", {}).get("content", "")
            parsed = _extract_json_from_text(content)
            return _from_llm_json(parsed)
        except (requests.RequestException, ValueError) as e:
            if attempt >= 3:
                logger.warning(
                    "云端 LLM 解析连续 %d 次失败，最终降级到本地正则: %s",
                    attempt,
                    str(e)[:200],
                )
                return _mock_parse(message_text)
            delay = 1.0 * (2 ** (attempt - 1))
            logger.warning("云端 LLM 第 %d/3 次失败: %s, %.1fs 后重试", attempt, str(e)[:150], delay)
            time.sleep(delay)
    # 理论上走不到这里，保险兜底
    return _mock_parse(message_text)


@router.post(
    "/parse",
    response_model=ParseResponse,
    dependencies=[Depends(require_write_token)],
)
def parse_house_text(req: ParseRequest) -> ParseResponse:
    """
    云端侧解析房产文本（需要写 Token 鉴权）。

    永远返回 HTTP 200（除 401 鉴权失败 / 422 参数错误外）：
      - 有有效 LLM_API_KEY → 调 Moonshot
      - Key 是占位符 / 调 Moonshot 3 次失败 → 降级本地正则
    """
    raw = (req.raw_text or "").strip()
    if not raw:
        return ParseResponse()
    try:
        return _call_llm(raw)
    except Exception as e:
        # 终极兜底：任何意料之外的异常都返回正则解析结果（再不济返回全 None）
        logger.exception("parse 接口终极兜底捕获异常，降级正则解析: %s", e)
        try:
            return _mock_parse(raw)
        except Exception:
            return ParseResponse()
