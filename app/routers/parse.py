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
    "【重要】支持【出售二手房 + 出租（整租/合租）】两类房源，出租消息里的「月租/年租/租金」等价于「总价」字段。"
    "请从用户提供的房产消息中提取结构化信息，以 JSON 格式返回。"
    "【硬性要求】禁止返回所有字段全 null！如果确实解析不到数字，小区名能猜就根据消息原文里的第一个地理名词填；"
    "如果所有字段都解析失败，也必须把【description】字段填上整段原文，这样用户在前端卡片里能看到原始消息文本。"
    "【硬性要求】只返回 JSON 对象，不要添加任何额外说明文字或 Markdown 标记。"
)

USER_PROMPT_TEMPLATE = """\
请从以下房产消息中提取信息（支持出售 / 出租 两类），返回 JSON：
{{
  "小区": "小区名称，没有填描述中出现的第一个地段/楼盘名；真的找不到填null",
  "面积": "数字+单位（平/平米/平方米/㎡），没有则null",
  "总价": "二手房=售价（数字+万）；出租=租金（如月租1800填'月租1800元'、年租2万填'年租2万元'）；没有则null",
  "户型": "如三室两厅两卫、三室一厅一卫、一居室；没有则null",
  "楼层": "如15/28层、2层、4楼、黄金楼层；真的找不到填null",
  "联系人": "姓名 + 电话 + 分机号（中介名+电话+分机号都拼上）；没有则null",
  "description": "【必填兜底】任何情况都必须填：原始消息的全文（多行保留换行），方便用户在前端看到原文"
}}

消息内容：
---BEGIN RAW---
{message_text}
---END RAW---
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
    description: Optional[str] = None


def _mock_parse(message_text: str) -> ParseResponse:
    """和本地端一致的离线正则解析，作为无 Key / LLM 失败时兜底
    【新增】2026-07-25：
      - 加出租场景：月租/年租/租金，匹配 "月租1800"、"月1800元"、"年租2万"
      - 小区名扩充：村/园/湾/街道/路号 也认（核电新村、金色海岸这种）
      - 楼层扩充：单独"X楼/X层/4楼/黄金楼层"也认（之前只认 "15/28 斜杠形式"，错过 "4楼、2层"）
      - 联系人扩充：中介公司名（XX房产、XX中介）+ 6 位以上分机号/短号（322850 这种）都拼进去
      - description 兜底必填：原文塞进去，前端卡片不空
    """
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
        r"出租[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,30}?(?:城|花园|华府|家园|里|湾|苑|府|公寓|小区|公馆|新村|园区|广场|家园|名邸|府邸|豪庭|雅苑|佳园|阳光|海岸|国际|中心|大厦|新城|首府|郡))",
        r"([\u4e00-\u9fa5A-Za-z0-9]{2,30}(?:城|花园|华府|家园|里|湾|苑|府|公寓|小区|公馆|新村|园区|广场|名邸|府邸|豪庭|雅苑|佳园|阳光|海岸|国际|中心|大厦|新城|首府|郡))",
        r"小区[:：]\s*([\u4e00-\u9fa5A-Za-z0-9]{2,30})",
    ])
    size = find([
        r"(\d+(?:\.\d+)?\s*(?:平|平米|平方米|㎡|m2|方))",
        r"(?:建筑面积|面积|使用面积|建面|占地)[:：]?\s*(\d+(?:\.\d+)?\s*(?:平|平米|平方米|㎡|m2|方)?)",
    ])
    # ---- 租金 / 总价（新增：出租优先）----
    price = find([
        r"((?:月租|月付|月租金|租金|每月)\s*\d{2,6}\s*(?:元|块|元/月|块钱)?(?:\s*[，。.]|$))",
        r"((?:年租|年付|年租金|每年)\s*\d+(?:\.\d+)?\s*(?:万|元|块|万元)?(?:\s*[，。.]|$))",
        r"((?:总价|售价|出售价)?\s*\d+(?:\.\d+)?\s*万)",
        r"(?:总价|售价|月租|年租|租金)[:：]\s*(\d+(?:\.\d+)?\s*(?:万?元?))",
    ])
    if price and (price.endswith("万") or price.endswith("元")):
        pass
    elif price and any(k in price for k in ("月租", "月付", "每月")):
        if "元" not in price:
            price = price + "元"
    elif price and not any(k in price for k in ("月租", "年租", "月付", "年付", "租金")):
        # 没带"万/元"的纯数字且不是出租的，默认补"万"
        if price and (price.isdigit() or (price[:-1].isdigit() and price[-1] in "0123456789.")):
            if not price.endswith("万"):
                price = price + "万"
    layout = find([
        r"([一二三四五六七八九十两213456]室[一二三四五六七八九十两12345]厅(?:[一二三四五六七八九十两12345]卫)?(?:[一二三四五六七八九十两12345]厨)?(?:[一二三四五六七八九十两12345]阳)?)",
        r"([一二三四五六七八九十两213456]房[一二三四五六七八九十两12345]厅(?:[一二三四五六七八九十两12345]卫)?)",
        r"(一居室|二居室|三居室|四居室|两居室|单人间|双人间|一室|两室|三室|四室|五室)",
    ])
    floor = find([
        r"(\d+\s*/\s*\d+\s*层?)",
        r"(?:楼层|层|楼)[:：]?\s*(第?\d+\s*(?:楼|层)?)",
        r"(黄金楼层|中间楼层|高楼层|低楼层|顶楼|底层|一楼|二楼|三楼|四楼|五楼|六楼|七楼|八楼|九楼|十楼)",
        r"(\d+楼(?!号|单元|栋|幢))",
        r"(\d+层(?!高|数))",
    ])
    # ---- 联系人 + 电话（11 位手机号） + 分机号（6 位纯数字 322850 这种） + 中介名（XX房产/XX中介）----
    phone_match = re.search(r"(1[3-9]\d{9})", text)
    # 6 位以上数字分机号（避免和 11 位电话混淆）
    ext_match = re.search(r"(?<!\d)(\d{6,8})(?!\d)", text)
    contact_parts: list[str] = []
    name = find([
        r"联系人[：:]\s*([\u4e00-\u9fa5A-Za-z]{2,6})",
        r"(?:联系人|联系电话|电话|看房|对接|置业顾问|经纪人|客服)[:：]?\s*([\u4e00-\u9fa5A-Za-z]{2,6})(?:\s*1[3-9]\d{9})",
    ])
    # 中介公司名：XX房产 / XX中介 / XX公寓 / XX地产
    agency_match = re.search(
        r"([\u4e00-\u9fa5]{2,10}(?:房产|中介|公寓|地产|房屋|不动产|置业|租售))",
        text,
    )
    if name:
        contact_parts.append(name)
    if agency_match:
        contact_parts.append(agency_match.group(1))
    if phone_match:
        contact_parts.append(phone_match.group(1))
    if ext_match:
        contact_parts.append("短号" + ext_match.group(1))
    contact = " ".join(contact_parts) if contact_parts else None

    # ---- description 兜底：永远塞原文 ----
    description = text[:2000] if text else None

    return ParseResponse(
        area=area, size=size, price=price, layout=layout, floor=floor, contact=contact,
        description=description,
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
        (("小区", "area", "community", "xiaoqu", "小区名称", "名称", "楼盘"), "area"),
        (("面积", "size", "建筑面积", "建面", "size_m2", "ping"), "size"),
        (("总价", "price", "售价", "租金", "月租", "年租", "总价万", "price_wan"), "price"),
        (("户型", "layout", "房型", "house_type"), "layout"),
        (("楼层", "floor", "层", "楼高", "louceng"), "floor"),
        (("联系人", "contact", "联系", "联系人电话", "lianxiren", "phone", "电话", "联系方式"), "contact"),
        (("description", "描述", "原文", "raw", "raw_text", "备注", "remark", "note", "content"), "description"),
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


def _ensure_desc_fallback(resp: ParseResponse, raw_text: str) -> ParseResponse:
    """【终极兜底】关键字段全空 / 缺 description 的，强制塞原文"""
    has_any = any([resp.area, resp.size, resp.price, resp.layout, resp.floor, resp.contact])
    desc = (resp.description or "").strip()
    if not desc:
        desc = (raw_text or "").strip()[:2000] or None
        resp.description = desc
    if not has_any:
        # 真的啥字段全空 → 把小区兜底写成"（待手动解析）"提示用户前端看到 description 看原文
        if not resp.area:
            resp.area = "（待手动解析）"
        logger.warning(
                "parse 结果关键字段空（小区/面积/总价/户型/楼层/联系人 全空，强制塞 description=原文，防止前端卡片空。原文前60字: %s",
                (raw_text or "")[:60],
            )
    return resp


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
            resp = _from_llm_json(parsed)
            return _ensure_desc_fallback(resp, message_text)
        except (requests.RequestException, ValueError) as e:
            if attempt >= 3:
                logger.warning(
                    "云端 LLM 解析连续 %d 次失败，最终降级到本地正则: %s",
                    attempt,
                    str(e)[:200],
                )
                return _ensure_desc_fallback(_mock_parse(message_text), message_text)
            delay = 1.0 * (2 ** (attempt - 1))
            logger.warning("云端 LLM 第 %d/3 次失败: %s, %.1fs 后重试", attempt, str(e)[:150], delay)
            time.sleep(delay)
    # 理论上走不到这里，保险兜底
    return _ensure_desc_fallback(_mock_parse(message_text), message_text)


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
        return ParseResponse(description=raw)
    try:
        resp = _call_llm(raw)
        return _ensure_desc_fallback(resp, raw)
    except Exception as e:
        # 终极兜底：任何意料之外的异常都返回正则解析结果（再不济返回全 None）
        logger.exception("parse 接口终极兜底捕获异常，降级正则解析: %s", e)
        try:
            return _ensure_desc_fallback(_mock_parse(raw), raw)
        except Exception:
            return ParseResponse(description=raw)
