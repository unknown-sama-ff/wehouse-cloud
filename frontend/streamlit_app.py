"""
WeHouse Streamlit 管理前端
- 侧边栏多条件筛选
- 顶部统计卡片
- 卡片式房源列表
- 操作按钮：标记已售 / 过期 / 删除 / 查看原始消息
- 手动添加房源表单
- CSV / Excel 导出

【性能优化要点】
- st.cache_data + 5 分钟 TTL：拉取数据不跨太平洋重复打
- 写操作（PATCH/DELETE/POST）成功后「先改内存缓存 + 清 cache key + rerun」
  （而不是删完了再让 load_all_houses 重新拉一遍，跨网慢）
- 默认 page_size=1000，减少分页网络请求数
"""

import io
import os
import re
from datetime import date, datetime
from typing import Optional

import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("API_BASE", "http://localhost:8000")
API_BASE = API_BASE.rstrip("/")
WRITE_TOKEN = os.getenv("WRITE_TOKEN", "").strip()
READ_PASSWORD = os.getenv("READ_PASSWORD", "").strip()

STATUS_LABELS = {"active": "在售", "sold": "已售", "expired": "过期", "deleted": "已删除"}
STATUS_VALUES = {"在售": "active", "已售": "sold", "过期": "expired", "已删除": "deleted"}

PAGE_SIZE = 20
# 拉数据时每页 500 条（兼顾单次拉取量 & 后端 page_size le=5000 上限；配合 cache 基本够用）
# 如果你房源超过 5000 条，会自动按 FETCH_PAGE_SIZE 继续翻页，不用改这里
FETCH_PAGE_SIZE = 500
# 拉取缓存 TTL：5 分钟内重复 load_all_houses 不打网络（除非写操作手动清缓存）
CACHE_TTL_SECONDS = 300
CACHE_KEY = "houses_cache_version"


# ---------- 工具函数 ----------

def _auth_headers(write: bool = False) -> dict:
    h = {"Content-Type": "application/json"}
    token = WRITE_TOKEN if write else (WRITE_TOKEN or READ_PASSWORD)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _safe_request(method: str, path: str, **kwargs):
    try:
        resp = requests.request(
            method,
            f"{API_BASE}{path}",
            headers=_auth_headers(method.upper() != "GET"),
            timeout=15,  # 从 30 改成 15，真失败早失败
            **kwargs,
        )
        return resp
    except requests.RequestException as e:
        st.error(f"请求后端失败：{e}")
        return None


def _extract_number(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", s)
    return float(m.group(0)) if m else None


def _to_wan_wan(price: Optional[str]) -> str:
    if not price:
        return "-"
    return price


# ---------- 缓存辅助：写操作后 bump version，下一次 load_all_houses 会绕过缓存 ----------

def _bump_cache() -> None:
    """让 st.cache_data 立即失效，下一次 load_all_houses 会真打网络（兜底用）"""
    st.session_state[CACHE_KEY] = st.session_state.get(CACHE_KEY, 0) + 1


def _current_cache_ver() -> int:
    return st.session_state.get(CACHE_KEY, 0)


# ---------- 页面初始化 ----------

st.set_page_config(page_title="WeHouse 房源管理", layout="wide", page_icon="🏠")
st.title("🏠 WeHouse 房产资源管理系统")
st.caption(f"后端 API：{API_BASE}")

if "page" not in st.session_state:
    st.session_state.page = 1
if CACHE_KEY not in st.session_state:
    st.session_state[CACHE_KEY] = 0


# ---------- 侧边栏筛选 ----------

with st.sidebar:
    st.header("🔍 筛选条件")
    f_area = st.text_input("小区名称（模糊匹配）", value="").strip()
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        f_min_p = st.text_input("最低总价（万）", value="").strip()
    with col_p2:
        f_max_p = st.text_input("最高总价（万）", value="").strip()

    layout_choices = ["一室", "二室", "三室", "四室", "其他"]
    f_layouts = st.multiselect("户型", layout_choices, default=[])

    status_choices = ["在售", "已售", "过期", "已删除"]
    f_statuses = st.multiselect("状态", status_choices, default=["在售", "已售", "过期"])

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        f_from = st.date_input("开始日期", value=None)
    with col_d2:
        f_to = st.date_input("结束日期", value=None)

    st.divider()
    if st.button("🔄 重置筛选", use_container_width=True):
        st.rerun()


# ---------- 拉取数据（加缓存！关键性能点） ----------

@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _load_from_remote(cache_version: int, area_filter: str) -> list[dict]:
    """
    真正打网络的函数。
    cache_version 参数存在的唯一目的：写操作 bump_cache 后，参数变化 → st.cache_data 重新执行。
    area_filter 参数：避免用户每次侧边栏改 area 都打网络，我们后端 GET /api/houses?area=xxx 直接服务端筛更省带宽。
    其他筛选（户型/价格/日期/状态）放在客户端二次筛，保持交互快。
    """
    all_items: list[dict] = []
    page = 1
    while True:
        params: dict = {"page": page, "page_size": FETCH_PAGE_SIZE}
        if area_filter:
            params["area"] = area_filter
        resp = _safe_request("GET", "/api/houses", params=params)
        if resp is None or resp.status_code != 200:
            if resp is not None:
                st.warning(f"后端返回异常 status={resp.status_code}: {resp.text[:200]}")
            break
        data = resp.json()
        items = data.get("items", [])
        all_items.extend(items)
        total_pages = data.get("total_pages", 0)
        if page >= total_pages or not items:
            break
        page += 1
    return all_items


def load_all_houses() -> list[dict]:
    """对外入口：优先缓存 + 支持版本失效"""
    ver = _current_cache_ver()
    # area 传服务端筛选（其他筛选走客户端），减少数据量
    return _load_from_remote(ver, f_area)


with st.spinner("正在加载房源数据..."):
    all_houses = load_all_houses()


# ---------- 客户端二次筛选 ----------

def client_side_filter(items: list[dict]) -> list[dict]:
    result = []
    for h in items:
        if f_layouts:
            layout = (h.get("layout") or "").strip()
            matched = False
            for choice in f_layouts:
                if choice == "其他":
                    if not any(k in layout for k in ["一室", "二室", "三室", "四室"]):
                        matched = True
                        break
                else:
                    if choice in layout:
                        matched = True
                        break
            if not matched:
                continue
        if f_statuses:
            codes = {STATUS_VALUES[s] for s in f_statuses}
            if (h.get("status") or "active") not in codes:
                continue
        if f_min_p or f_max_p:
            p = _extract_number(h.get("price"))
            try:
                if f_min_p and (p is None or p < float(f_min_p)):
                    continue
                if f_max_p and (p is None or p > float(f_max_p)):
                    continue
            except ValueError:
                pass
        if f_from or f_to:
            try:
                ca = h.get("created_at")
                if ca:
                    d = datetime.fromisoformat(ca.replace("Z", "")).date()
                    if f_from and d < f_from:
                        continue
                    if f_to and d > f_to:
                        continue
            except Exception:
                pass
        result.append(h)
    return result


houses = client_side_filter(all_houses)
houses.sort(key=lambda h: h.get("created_at") or "", reverse=True)


# ---------- 统计卡片 ----------

def _count_status(items: list[dict], status: str) -> int:
    return sum(1 for h in items if h.get("status") == status)


c1, c2, c3, c4 = st.columns(4)
with c1:
    st.metric("总房源数", len(houses))
with c2:
    st.metric("在售", _count_status(houses, "active"))
with c3:
    st.metric("已售", _count_status(houses, "sold"))
with c4:
    st.metric("过期", _count_status(houses, "expired"))

st.divider()


# ---------- 操作函数（优化关键：成功后本地直接改列表 + bump cache，不跨网重拉！） ----------

def _mutate_local_list(house_id: int, new_status: Optional[str], remove: bool = False) -> None:
    """
    写操作成功后，在本地的 all_houses 里立即体现，用户视觉上「秒改」。
    同时 bump_cache 版本：下次刷新/手动重置时才会从后端重新拉（保证数据一致性兜底）。
    """
    for i, h in enumerate(all_houses):
        if h.get("id") == house_id:
            if remove:
                all_houses.pop(i)
            elif new_status is not None:
                h["status"] = new_status
                h["updated_at"] = datetime.now().isoformat()
            break
    _bump_cache()


def update_status(house_id: int, new_status: str, label: str) -> None:
    resp = _safe_request("PATCH", f"/api/houses/{house_id}", json={"status": new_status})
    if resp is not None and 200 <= resp.status_code < 300:
        _mutate_local_list(house_id, new_status=new_status)
        st.success(f"已标记为 {label}")
        st.rerun()
    else:
        msg = resp.text[:200] if resp is not None else "请求失败"
        st.error(f"操作失败：{msg}")


def soft_delete(house_id: int) -> None:
    resp = _safe_request("DELETE", f"/api/houses/{house_id}")
    if resp is not None and 200 <= resp.status_code < 300:
        # 软删除：后端把 status 改成 deleted，我们本地也同步成 deleted（而不是从列表里移除，避免筛选「已删除」找不到）
        _mutate_local_list(house_id, new_status="deleted")
        st.success("已删除")
        st.rerun()
    else:
        msg = resp.text[:200] if resp is not None else "请求失败"
        st.error(f"删除失败：{msg}")


# ---------- 分页 ----------

total_pages = max(1, (len(houses) + PAGE_SIZE - 1) // PAGE_SIZE)
if st.session_state.page > total_pages:
    st.session_state.page = 1

start = (st.session_state.page - 1) * PAGE_SIZE
end = start + PAGE_SIZE
page_items = houses[start:end]


# ---------- 手动添加房源 ----------

with st.expander("➕ 手动添加房源", expanded=False):
    with st.form("manual_add", clear_on_submit=False):
        m1, m2 = st.columns(2)
        with m1:
            s_area = st.text_input("小区名称")
            s_size = st.text_input("面积（如 118.5平）")
            s_layout = st.text_input("户型（如 三室两厅）")
        with m2:
            s_price = st.text_input("总价（如 328万）")
            s_floor = st.text_input("楼层（如 15/28）")
            s_contact = st.text_input("联系人（姓名+电话）")
        s_source = st.text_input("来源", value="手动录入")
        s_raw = st.text_area("原始消息文本", height=100, placeholder="可填写完整的原始消息...")
        submitted = st.form_submit_button("提交添加")
        if submitted:
            if not s_raw.strip():
                s_raw = f"{s_area} {s_layout} {s_size} {s_price} {s_floor} {s_contact}".strip()
            payload = {
                "source": s_source or "手动录入",
                "raw_text": s_raw or "(空)",
                "area": s_area or None,
                "size": s_size or None,
                "price": s_price or None,
                "layout": s_layout or None,
                "floor": s_floor or None,
                "contact": s_contact or None,
                "status": "active",
            }
            resp = _safe_request("POST", "/api/houses", json=payload)
            if resp is not None and 200 <= resp.status_code < 300:
                # POST 成功：尝试把后端返回的新记录（含 id/created_at）插在本地列表最前面
                try:
                    new_item = resp.json()
                    if isinstance(new_item, dict) and "id" in new_item:
                        all_houses.insert(0, new_item)
                except Exception:
                    pass
                _bump_cache()
                st.success("添加成功")
                st.rerun()
            else:
                msg = resp.text[:200] if resp is not None else "请求失败"
                st.error(f"添加失败：{msg}")


# ---------- 导出 ----------

col_export1, col_export2, _ = st.columns([2, 2, 10])

with col_export1:
    if st.button("📥 导出 CSV", use_container_width=True):
        if houses:
            df = pd.DataFrame(houses)
            rename = {
                "id": "ID", "source": "来源", "raw_text": "原始消息",
                "area": "小区", "size": "面积", "price": "总价",
                "layout": "户型", "floor": "楼层", "contact": "联系人",
                "status": "状态", "created_at": "创建时间", "updated_at": "更新时间",
            }
            df = df.rename(columns=rename)
            if "状态" in df.columns:
                df["状态"] = df["状态"].map(lambda s: STATUS_LABELS.get(s, s))
            buf = io.BytesIO()
            df.to_csv(buf, index=False, encoding="utf-8-sig")
            st.download_button(
                "点击下载 CSV",
                data=buf.getvalue(),
                file_name=f"wehouse_houses_{date.today()}.csv",
                mime="text/csv",
            )
        else:
            st.info("暂无可导出数据")

with col_export2:
    if st.button("📊 导出 Excel", use_container_width=True):
        if houses:
            df = pd.DataFrame(houses)
            rename = {
                "id": "ID", "source": "来源", "raw_text": "原始消息",
                "area": "小区", "size": "面积", "price": "总价",
                "layout": "户型", "floor": "楼层", "contact": "联系人",
                "status": "状态", "created_at": "创建时间", "updated_at": "更新时间",
            }
            df = df.rename(columns=rename)
            if "状态" in df.columns:
                df["状态"] = df["状态"].map(lambda s: STATUS_LABELS.get(s, s))
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="房源")
            st.download_button(
                "点击下载 Excel",
                data=buf.getvalue(),
                file_name=f"wehouse_houses_{date.today()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            st.info("暂无可导出数据")


# ---------- 房源列表 ----------

st.subheader(f"📋 房源列表（共 {len(houses)} 条，第 {st.session_state.page}/{total_pages} 页）")

if not page_items:
    st.info("暂无符合条件的房源")
else:
    for h in page_items:
        status = h.get("status") or "active"
        label = STATUS_LABELS.get(status, status)
        badge_color = {
            "active": "🟢",
            "sold": "🔵",
            "expired": "🟠",
            "deleted": "⚫",
        }.get(status, "⚪")

        with st.container(border=True):
            hc1, hc2 = st.columns([12, 1])
            with hc1:
                title_parts = [
                    badge_color,
                    f"**{h.get('area') or '（未识别小区）'}**",
                    h.get("layout") or "",
                    h.get("size") or "",
                ]
                st.markdown(" &nbsp;·&nbsp; ".join([p for p in title_parts if p]))
                meta_cols = st.columns(4)
                with meta_cols[0]:
                    st.metric("总价", _to_wan_wan(h.get("price")))
                with meta_cols[1]:
                    st.metric("面积", h.get("size") or "-")
                with meta_cols[2]:
                    st.metric("楼层", h.get("floor") or "-")
                with meta_cols[3]:
                    st.metric("状态", label)
                st.caption(
                    f"来源：{h.get('source') or '-'} ｜ "
                    f"联系人：{h.get('contact') or '-'} ｜ "
                    f"接收时间：{h.get('created_at') or '-'}"
                )
            with hc2:
                if st.button("📝", key=f"raw_{h['id']}", help="查看原始消息"):
                    with st.expander(f"原始消息 #{h['id']}", expanded=True):
                        st.text(h.get("raw_text") or "(空)")

            bc1, bc2, bc3, bc4 = st.columns([2, 2, 2, 2])
            with bc1:
                if status != "sold":
                    if st.button("✅ 标记已售", key=f"sold_{h['id']}", use_container_width=True):
                        update_status(h["id"], "sold", "已售")
            with bc2:
                if status != "expired":
                    if st.button("⏰ 标记过期", key=f"exp_{h['id']}", use_container_width=True):
                        update_status(h["id"], "expired", "过期")
            with bc3:
                if status not in ("active",):
                    if st.button("↩️ 恢复在售", key=f"act_{h['id']}", use_container_width=True):
                        update_status(h["id"], "active", "在售")
            with bc4:
                if st.button("🗑️ 删除", key=f"del_{h['id']}", use_container_width=True):
                    soft_delete(h["id"])


# ---------- 分页控件 ----------

st.divider()
pc1, pc2, pc3, pc4 = st.columns([1, 2, 2, 1])
with pc2:
    if st.button("⬅️ 上一页", disabled=(st.session_state.page <= 1), use_container_width=True):
        st.session_state.page -= 1
        st.rerun()
with pc3:
    if st.button("下一页 ➡️", disabled=(st.session_state.page >= total_pages), use_container_width=True):
        st.session_state.page += 1
        st.rerun()
