"""
房源 CRUD 路由
- POST   /api/houses           写入（需写 token）
- POST   /api/houses/batch     批量写入（预留扩展，需写 token）
- GET    /api/houses           列表查询（分页 + 多条件筛选）
- GET    /api/houses/{id}      单条详情
- PATCH  /api/houses/{id}      更新
- DELETE /api/houses/{id}      软删除（status=deleted）
"""

import math
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import House
from app.routers.auth import require_read_access, require_write_token
from app import schemas

router = APIRouter(prefix="/api/houses", tags=["houses"])


def _extract_number(value: Optional[str]) -> Optional[float]:
    """从 '328万' / '118.5平' 这类字符串中提取首个数字，用于比较筛选"""
    if not value:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", value)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


@router.post(
    "",
    response_model=schemas.HouseResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_write_token)],
)
def create_house(
    payload: schemas.HouseCreate,
    db: Session = Depends(get_db),
) -> House:
    """
    接收本地采集端推送的新房源
    写操作需要 Header: Authorization: Bearer {SECRET_TOKEN}
    """
    row = House(**payload.model_dump(exclude_unset=True))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post(
    "/batch",
    response_model=schemas.BatchCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_write_token)],
)
def batch_create_houses(
    payload: schemas.BatchCreateRequest,
    db: Session = Depends(get_db),
) -> schemas.BatchCreateResponse:
    """
    批量导入接口（预留扩展）
    逐条插入，单条失败不影响其它
    """
    created = 0
    errors: list[str] = []
    for i, item in enumerate(payload.items):
        try:
            row = House(**item.model_dump(exclude_unset=True))
            db.add(row)
            db.commit()
            created += 1
        except Exception as e:
            db.rollback()
            errors.append(f"第{i+1}条: {e}")
    return schemas.BatchCreateResponse(
        created=created,
        failed=len(errors),
        errors=errors,
    )


@router.get(
    "",
    response_model=schemas.HouseListResponse,
    dependencies=[Depends(require_read_access)],
)
def list_houses(
    area: Optional[str] = Query(None, description="小区名，模糊匹配"),
    min_price: Optional[float] = Query(None, description="最低总价（单位：万）"),
    max_price: Optional[float] = Query(None, description="最高总价（单位：万）"),
    layout: Optional[str] = Query(None, description="户型关键词，如 '三室'"),
    status: Optional[str] = Query(None, description="状态：active/sold/expired/deleted"),
    sort: str = Query("created_at", description="排序字段"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=5000, description="每页条数（最大 5000，避免拉全表 OOM）"),
    db: Session = Depends(get_db),
) -> schemas.HouseListResponse:
    """
    房源列表查询（分页 + 条件筛选）
    """
    valid_sort_fields = {"created_at", "updated_at", "id", "area", "price"}
    sort_field = sort if sort in valid_sort_fields else "created_at"

    conditions = [House.status != "deleted"]
    if area:
        conditions.append(House.area.ilike(f"%{area}%"))
    if layout:
        conditions.append(House.layout.ilike(f"%{layout}%"))
    if status:
        conditions.append(House.status == status)

    count_stmt = select(func.count(House.id)).where(and_(*conditions))
    total = int(db.execute(count_stmt).scalar_one() or 0)

    stmt = select(House).where(and_(*conditions))
    order_expr = desc(getattr(House, sort_field)) if order == "desc" else getattr(House, sort_field)
    stmt = stmt.order_by(order_expr)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = list(db.execute(stmt).scalars().all())

    if min_price is not None or max_price is not None:
        filtered: list[House] = []
        for r in rows:
            p = _extract_number(r.price)
            if min_price is not None and (p is None or p < min_price):
                continue
            if max_price is not None and (p is None or p > max_price):
                continue
            filtered.append(r)
        rows = filtered

    total_pages = math.ceil(total / page_size) if page_size > 0 else 0
    return schemas.HouseListResponse(
        items=rows,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/{house_id}",
    response_model=schemas.HouseResponse,
    dependencies=[Depends(require_read_access)],
)
def get_house(
    house_id: int,
    db: Session = Depends(get_db),
) -> House:
    """单条房源详情"""
    row = db.get(House, house_id)
    if row is None or row.status == "deleted":
        raise HTTPException(status_code=404, detail="房源不存在或已删除")
    return row


@router.patch(
    "/{house_id}",
    response_model=schemas.HouseResponse,
    dependencies=[Depends(require_write_token)],
)
def update_house(
    house_id: int,
    payload: schemas.HouseUpdate,
    db: Session = Depends(get_db),
) -> House:
    """更新房源字段（PATCH 语义：仅传需要修改的字段）"""
    row = db.get(House, house_id)
    if row is None:
        raise HTTPException(status_code=404, detail="房源不存在")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete(
    "/{house_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_write_token)],
)
def delete_house(
    house_id: int,
    db: Session = Depends(get_db),
) -> None:
    """
    软删除：将 status 改为 'deleted'
    如需物理删除请直接操作数据库
    """
    row = db.get(House, house_id)
    if row is None:
        raise HTTPException(status_code=404, detail="房源不存在")
    row.status = "deleted"
    db.commit()
