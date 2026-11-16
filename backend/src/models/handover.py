from sqlalchemy import String, JSON, Double, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Handover(Base):
    """一条样品跨库交接单，整单在途、整单签收，差异进入争议。"""
    __tablename__ = 'handovers'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True, default='')
    batch_no: Mapped[str] = mapped_column(String(80), index=True)
    from_library: Mapped[str] = mapped_column(String(80))
    to_library: Mapped[str] = mapped_column(String(80))
    sample_count: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(500), default='')
    initiator_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    initiator_email: Mapped[str] = mapped_column(String(150))
    initiator_name: Mapped[str] = mapped_column(String(80))
    receiver_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    receiver_email: Mapped[str] = mapped_column(String(150))
    receiver_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default='in_transit')
    # 仅在途/争议单占用该批号；签收、撤回或裁定后置空，允许多个历史单存在。
    active_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    received_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difference_note: Mapped[str] = mapped_column(String(500), default='')
    withdraw_reason: Mapped[str] = mapped_column(String(500), default='')
    resolution: Mapped[str] = mapped_column(String(30), default='')
    resolution_reason: Mapped[str] = mapped_column(String(1000), default='')
    resolved_by_email: Mapped[str] = mapped_column(String(150), default='')
    resolved_by_name: Mapped[str] = mapped_column(String(80), default='')
    created_at: Mapped[float] = mapped_column(Double)
    signed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    withdrawn_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    disputed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    resolved_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class HandoverEvent(Base):
    """交接链事件，按 id 顺序组成可追查的时间线。"""
    __tablename__ = 'handover_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    handover_id: Mapped[int] = mapped_column(ForeignKey('handovers.id'), index=True)
    action: Mapped[str] = mapped_column(String(40))
    actor_email: Mapped[str] = mapped_column(String(150))
    actor_name: Mapped[str] = mapped_column(String(80), default='')
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[float] = mapped_column(Double)


class SampleCustody(Base):
    """批号的当前保管库与持有人，是交接归属的唯一事实来源。"""
    __tablename__ = 'sample_custody'
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(80), unique=True)
    library: Mapped[str] = mapped_column(String(80))
    holder_email: Mapped[str] = mapped_column(String(150), default='')
    holder_name: Mapped[str] = mapped_column(String(80), default='')
    handover_id: Mapped[int | None] = mapped_column(ForeignKey('handovers.id'), nullable=True)
    updated_at: Mapped[float] = mapped_column(Double)


class BatchGate(Base):
    """批号级事务闸门：在途/争议状态与分析入队使用同一把锁裁定。"""
    __tablename__ = 'batch_gates'
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_no: Mapped[str] = mapped_column(String(80), unique=True)


class HandoverRequest(Base):
    """带 Idempotency-Key 的操作结果，重试直接回放，不再重复转库。"""
    __tablename__ = 'handover_requests'
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    action: Mapped[str] = mapped_column(String(40))
    handover_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_email: Mapped[str] = mapped_column(String(150))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('key', name='uq_handover_request_key'),)
