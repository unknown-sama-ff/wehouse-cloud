"""
云端 LLM 解析路由（可选功能）
- 本地端如果配置了 use_cloud_parser=true，会通过此接口把 raw_text 发上来解析
- 避免把 Moonshot API Key 保存在本地电脑
- Moonshot 参数从环境变量读取：LLM_API_URL / LLM_API_KEY / LLM_MODEL
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

LLM_API_URL = os.getenv("LLM_API_URL", "https://api.moonshot.cn/v1/chat/completions").strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "kimi-latest").strip()

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
    """和本地端一致的离线正则解析，作为无 Key 时的兜底"""
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
    """调用 Moonshot 并做 3 次指数退避重试"""
    if not LLM_API_KEY or LLM_API_KEY == "sk-xxx" or LLM_API_KEY.startswith("your-"):
        logger.warning("LLM_API_KEY 未配置（或是占位符），自动降级为本地正则解析")
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
    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            resp = requests.post(
                LLM_API_URL,
                headers={
                    "Authorization": f"Bearer {LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=90,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise ValueError("LLM 响应缺少 choices")
            content = choices[0].get("message", {}).get("content", "")
            parsed = _extract_json_from_text(content)
            return _from_llm_json(parsed)
        except (requests.RequestException, ValueError) as e:
            last_exc = e
            if attempt >= 3:
                break
            delay = 1.0 * (2 ** (attempt - 1))
            logger.warning("云端 LLM 第 %d/3 次失败: %s, %.1fs 后重试", attempt, e, delay)
            time.sleep(delay)
    raise RuntimeError(f"云端 LLM 解析失败: {last_exc}")


@router.post(
    "/parse",
    response_model=ParseResponse,
    dependencies=[Depends(require_write_token)],
)
def parse_house_text(req: ParseRequest) -> ParseResponse:
    """
    云端侧解析房产文本（需要写 Token 鉴权）。

    本地采集端可选择把 raw_text 直接发上来，云端用 Railway Variables 里的
    LLM_API_KEY 调用 Moonshot，避免本地电脑保存任何密钥。
    """
    try:
        result = _call_llm(req.raw_text)
        logger.info("云端解析完成: %s", result.model_dump())
        return result
    except Exception as e:
        logger.exception("云端解析异常: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM 解析失败: {e}",
        )
