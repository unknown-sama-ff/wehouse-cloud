"""
Pydantic 数据校验模型（schemas）
用于 FastAPI 请求体与响应体的类型校验与序列化
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class HouseBase(BaseModel):
    """房源通用字段"""

    source: str = Field(..., max_length=255, description="消息来源（群名/联系人）")
    raw_text: str = Field(..., description="原始消息全文")
    area: Optional[str] = Field(None, max_length=255, description="小区名称")
    size: Optional[str] = Field(None, max_length=50, description="面积，如 118.5平")
    price: Optional[str] = Field(None, max_length=50, description="总价，如 328万")
    layout: Optional[str] = Field(None, max_length=50, description="户型，如 三室两厅")
    floor: Optional[str] = Field(None, max_length=50, description="楼层，如 15/28")
    contact: Optional[str] = Field(None, max_length=255, description="联系人与电话")
    status: str = Field("active", max_length=20, description="active/sold/expired/deleted")


class HouseCreate(HouseBase):
    """创建房源请求体（本地采集端推送使用）"""

    pass


class HouseUpdate(BaseModel):
    """更新房源请求体（所有字段可选，PATCH 语义）"""

    source: Optional[str] = Field(None, max_length=255)
    raw_text: Optional[str] = None
    area: Optional[str] = Field(None, max_length=255)
    size: Optional[str] = Field(None, max_length=50)
    price: Optional[str] = Field(None, max_length=50)
    layout: Optional[str] = Field(None, max_length=50)
    floor: Optional[str] = Field(None, max_length=50)
    contact: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = Field(None, max_length=20)


class HouseResponse(HouseBase):
    """房源响应体"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class HouseListResponse(BaseModel):
    """列表查询响应（含分页信息）"""

    items: list[HouseResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class BatchCreateRequest(BaseModel):
    """批量导入请求体（预留扩展接口）"""

    items: list[HouseCreate] = Field(..., min_length=1, max_length=500)


class BatchCreateResponse(BaseModel):
    """批量导入响应"""

    created: int
    failed: int
    errors: list[str] = Field(default_factory=list)
