"""样品跨库交接链：整单在途、整单签收、差异争议与第三方裁定。

并发口径：
- 同一批号的交接发起、签收、撤回、裁定与分析入队都会先锁定 batch_gates 行，
  在 MySQL 上由 SELECT ... FOR UPDATE 串行化；状态迁移再用带状态条件的
  UPDATE 兜底，因此并发签收/撤回只会有一个归属成立。
- 只有 active_key 非空的单占用批号，唯一约束保证同批不会同时存在两张在途/争议单。
"""
import time
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from src.constants.enums import HANDOVER_STATUS_LABELS, HANDOVER_RESOLUTION_LABELS, LIBRARIES
from src.models import Handover, HandoverEvent, HandoverRequest, SampleCustody, BatchGate, User
from src.services.audit import record

ACTIVE_STATUSES = ('in_transit', 'dispute')
WRITE_ROLES = ('admin', 'analyst')


def _now():
    return time.time()


def _event(db, handover, action, user, detail=None):
    db.add(HandoverEvent(handover_id=handover.id, action=action, actor_email=user.email,
                         actor_name=user.name, detail=detail or {}, at=_now()))


def lock_batch(db, batch_no):
    """锁定批号闸门；不存在时先补建。交接与分析入队共用这一把锁。"""
    gate = db.scalar(select(BatchGate).where(BatchGate.batch_no == batch_no).with_for_update())
    if gate is None:
        try:
            with db.begin_nested():
                db.add(BatchGate(batch_no=batch_no))
        except IntegrityError:
            pass
        gate = db.scalar(select(BatchGate).where(BatchGate.batch_no == batch_no).with_for_update())
    return gate


def active_for_batch(db, batch_no):
    # 使用锁定读读取最新已提交数据，避免 REPEATABLE READ 旧快照漏掉并发发起的交接单。
    query = (select(Handover).where(Handover.active_key == batch_no)
             .with_for_update().execution_options(populate_existing=True))
    return db.scalar(query)


def block_reason(db, batch_no):
    """分析入队前调用：返回在途/争议单，没有则 None。"""
    lock_batch(db, batch_no)
    return active_for_batch(db, batch_no)


def _custody(db, batch_no, library=None, user=None):
    custody = db.scalar(select(SampleCustody).where(SampleCustody.batch_no == batch_no)
                        .with_for_update().execution_options(populate_existing=True))
    if custody is None:
        custody = SampleCustody(batch_no=batch_no, library=library or LIBRARIES[0],
                                holder_email=user.email if user else '', holder_name=user.name if user else '',
                                handover_id=None, updated_at=_now())
        db.add(custody)
        db.flush()
    return custody


def cleanup_request_keys(db, older_than_days=7):
    """幂等回放记录只保留近期窗口，避免无限增长；不影响交接事件链。"""
    cutoff = _now() - older_than_days * 86400
    deleted = db.query(HandoverRequest).filter(HandoverRequest.created_at < cutoff).delete()
    return deleted


def _check_libraries(from_library, to_library):
    if from_library not in LIBRARIES or to_library not in LIBRARIES:
        raise HTTPException(422, '保管库不在可选库位清单内')
    if from_library == to_library:
        raise HTTPException(422, '交出库与接收库不能相同')


def initiate(db, data, user):
    batch_no = data.batch_no.strip()
    _check_libraries(data.from_library, data.to_library)
    if user.role != 'analyst':
        raise HTTPException(403, '样品交出须由分析员经办发起；负责人只负责争议裁定，以保持三方分工')
    receiver = db.get(User, data.receiver_id)
    if not receiver or receiver.role != 'analyst':
        raise HTTPException(422, '接收员须为分析员经办账号；负责人不参与实物交接，以便争议时保持第三方身份')
    if receiver.id == user.id:
        raise HTTPException(422, '接收员不能是发起人本人，请指定其他经办账号')
    lock_batch(db, batch_no)
    if active_for_batch(db, batch_no):
        raise HTTPException(409, '该批号已有在途或争议交接单，请先完成或裁定当前单')
    custody = _custody(db, batch_no, data.from_library, user)
    if custody.library != data.from_library:
        raise HTTPException(409, f'该批号当前保管在「{custody.library}」，与所选交出库不一致')
    order = Handover(batch_no=batch_no, from_library=data.from_library, to_library=data.to_library,
                     sample_count=data.sample_count, note=data.note, status='in_transit',
                     active_key=batch_no, initiator_id=user.id, initiator_email=user.email,
                     initiator_name=user.name, receiver_id=receiver.id, receiver_email=receiver.email,
                     receiver_name=receiver.name, created_at=_now())
    try:
        with db.begin_nested():
            db.add(order)
            db.flush()
    except IntegrityError:
        raise HTTPException(409, '该批号已有在途或争议交接单，请先完成或裁定当前单')
    order.order_no = f'HO-{order.id:05d}'
    custody.handover_id = order.id
    custody.updated_at = _now()
    _event(db, order, 'initiated', user, {
        'batch_no': batch_no,
        'from_library': data.from_library,
        'to_library': data.to_library,
        'sample_count': data.sample_count,
        'receiver': receiver.email,
    })
    db.flush()
    return order


def _load_locked(db, handover_id):
    order = db.get(Handover, handover_id)
    if not order:
        raise HTTPException(404, '交接单不存在')
    lock_batch(db, order.batch_no)
    # 取得批号闸门后再做一次锁定读，确保看到并发事务已提交的最新状态。
    return db.scalar(select(Handover).where(Handover.id == handover_id)
                     .with_for_update().execution_options(populate_existing=True))


def _transition(db, order, expected, **values):
    result = db.execute(update(Handover).where(Handover.id == order.id, Handover.status == expected)
                        .values(**values))
    if result.rowcount != 1:
        db.rollback()
        fresh = db.get(Handover, order.id)
        label = HANDOVER_STATUS_LABELS.get(fresh.status, fresh.status) if fresh else '未知'
        raise HTTPException(409, f'交接单状态已变化（当前：{label}），请刷新后重试')
    db.flush()


def sign(db, handover_id, data, user):
    order = _load_locked(db, handover_id)
    if order.status != 'in_transit':
        raise HTTPException(409, f'该交接单当前为「{HANDOVER_STATUS_LABELS.get(order.status, order.status)}」，不能签收')
    if user.role not in WRITE_ROLES:
        raise HTTPException(403, '观察员不能签收交接单')
    if order.receiver_id != user.id:
        raise HTTPException(403, '只有该单指定的接收员可以核对签收，负责人也不能代签')
    if data.received_count < 0:
        raise HTTPException(422, '实收数量不能为负')
    now = _now()
    if data.received_count != order.sample_count:
        note = (data.difference_note or '').strip()
        if len(note) < 2:
            raise HTTPException(422, '实收数量与应交数量不一致，必须填写差异说明并转入争议')
        _transition(db, order, 'in_transit', status='dispute', received_count=data.received_count,
                    difference_note=note, disputed_at=now)
        custody = _custody(db, order.batch_no, order.to_library)
        custody.library = order.to_library
        custody.holder_email = user.email
        custody.holder_name = user.name
        custody.handover_id = order.id
        custody.updated_at = now
        _event(db, order, 'disputed', user, {'received_count': data.received_count,
                                             'sample_count': order.sample_count, 'note': note})
        db.flush()
        return _reload(db, order.id)
    _transition(db, order, 'in_transit', status='signed', active_key=None,
                received_count=data.received_count, signed_at=now)
    custody = _custody(db, order.batch_no, order.to_library)
    custody.library = order.to_library
    custody.holder_email = user.email
    custody.holder_name = user.name
    custody.handover_id = None
    custody.updated_at = now
    _event(db, order, 'signed', user, {'received_count': data.received_count,
                                       'to_library': order.to_library})
    db.flush()
    return _reload(db, order.id)


def withdraw(db, handover_id, data, user):
    order = _load_locked(db, handover_id)
    if order.status != 'in_transit':
        raise HTTPException(409, f'该交接单当前为「{HANDOVER_STATUS_LABELS.get(order.status, order.status)}」，不能撤回')
    if order.initiator_id != user.id:
        raise HTTPException(403, '只有发起人可以撤回未签收的交接单')
    now = _now()
    _transition(db, order, 'in_transit', status='withdrawn', active_key=None,
                withdrawn_at=now, withdraw_reason=(data.reason or '').strip())
    custody = _custody(db, order.batch_no, order.from_library)
    # 未签收即撤回：样品从未离开交出库，归属完整回滚。
    custody.library = order.from_library
    custody.holder_email = order.initiator_email
    custody.holder_name = order.initiator_name
    custody.handover_id = None
    custody.updated_at = now
    _event(db, order, 'withdrawn', user, {'reason': (data.reason or '').strip()})
    db.flush()
    return _reload(db, order.id)


def resolve(db, handover_id, data, user):
    order = _load_locked(db, handover_id)
    if order.status != 'dispute':
        raise HTTPException(409, f'该交接单当前为「{HANDOVER_STATUS_LABELS.get(order.status, order.status)}」，无需裁定')
    if user.role != 'admin':
        raise HTTPException(403, '争议须由业务负责人裁定')
    if user.id in (order.initiator_id, order.receiver_id):
        raise HTTPException(403, '裁定人不得是交接双方，请由第三方负责人处理')
    reason = data.reason.strip()
    if len(reason) < 5:
        raise HTTPException(422, '裁定必须写明不少于5个字的原因')
    if data.resolution not in HANDOVER_RESOLUTION_LABELS:
        raise HTTPException(422, '裁定结果仅支持接收留样或退回交出方')
    now = _now()
    _transition(db, order, 'dispute', status='resolved', active_key=None,
                resolution=data.resolution, resolution_reason=reason,
                resolved_by_email=user.email, resolved_by_name=user.name, resolved_at=now)
    custody = _custody(db, order.batch_no, order.to_library)
    if data.resolution == 'accept':
        custody.library = order.to_library
        custody.holder_email = order.receiver_email
        custody.holder_name = order.receiver_name
    else:
        custody.library = order.from_library
        custody.holder_email = order.initiator_email
        custody.holder_name = order.initiator_name
    custody.handover_id = None
    custody.updated_at = now
    _event(db, order, 'resolved', user, {'resolution': data.resolution, 'reason': reason,
                                         'resolution_label': HANDOVER_RESOLUTION_LABELS[data.resolution]})
    db.flush()
    return _reload(db, order.id)


def _reload(db, handover_id):
    db.flush()
    return db.scalar(select(Handover).where(Handover.id == handover_id)
                     .execution_options(populate_existing=True))


def perform(db, key, action, user, operation, scope_id=None):
    """执行一次状态操作并落审计；同一 Idempotency-Key 重试直接回放，不重复转库。

    operation() 返回 (status_code, payload, audit_detail)。
    scope_id 为操作的交接单 ID，防止同一键被复用到另一张单。
    """
    cached = None
    if key:
        cached = db.scalar(select(HandoverRequest).where(HandoverRequest.key == key))
        if cached:
            if (cached.action != action or cached.actor_email != user.email
                    or (scope_id is not None and cached.handover_id != scope_id)):
                raise HTTPException(409, '该幂等键已被其他操作使用，请重新发起')
            return cached.status_code, cached.response
    try:
        status_code, payload, audit_detail = operation()
        record(db, user.email, 'handover.' + action, audit_detail)
        if key:
            db.add(HandoverRequest(key=key, action=action, handover_id=audit_detail.get('id'),
                                   actor_email=user.email, status_code=status_code,
                                   response=payload, created_at=_now()))
        db.commit()
        return status_code, payload
    except HTTPException:
        db.rollback()
        if key:
            replay = db.scalar(select(HandoverRequest).where(HandoverRequest.key == key))
            if (replay and replay.action == action and replay.actor_email == user.email
                    and (scope_id is None or replay.handover_id == scope_id)):
                return replay.status_code, replay.response
        raise
    except IntegrityError:
        db.rollback()
        if key:
            replay = db.scalar(select(HandoverRequest).where(HandoverRequest.key == key))
            if (replay and replay.action == action and replay.actor_email == user.email
                    and (scope_id is None or replay.handover_id == scope_id)):
                return replay.status_code, replay.response
        raise HTTPException(409, '批号交接状态已变化，请刷新后重试')
    except Exception:
        db.rollback()
        raise


def recover(db):
    """服务重启后恢复交接：在途单继续有效，并校对批号归属；重复执行不重复转库。

    以批号为单位取最新一张单推导应然归属：有在途/争议单以它为最高优先，
    否则取最近一张终态单。归属已一致时不产生任何写入。
    """
    repaired = 0
    orders = list(db.scalars(select(Handover).order_by(Handover.id)))
    latest = {}
    active_by_batch = {}
    for order in orders:
        latest[order.batch_no] = order
        if order.status in ACTIVE_STATUSES:
            active_by_batch[order.batch_no] = order
    for batch_no, order in latest.items():
        # 在途/争议单优先级最高；否则以最近一张终态单推导应然归属。
        active_order = active_by_batch.get(batch_no)
        source = active_order or order
        # 清理残留的唯一占用键；正常提交后这里不会命中。
        for o in orders:
            if o.batch_no == batch_no and o.active_key == batch_no and o is not active_order:
                o.active_key = None
                repaired += 1
        if active_order is not None and active_order.active_key != batch_no:
            conflict = db.scalar(select(Handover).where(Handover.active_key == batch_no,
                                                        Handover.id != active_order.id))
            if conflict is None:
                active_order.active_key = batch_no
                repaired += 1
        if source.status == 'in_transit':
            # 在途不代表已完成转库：保管库回到交出库，持有人保持原状。
            library, email, name = source.from_library, None, None
            active_id = source.id
        elif source.status == 'dispute':
            library, email, name = source.to_library, source.receiver_email, source.receiver_name
            active_id = source.id
        elif source.status == 'signed' or source.resolution == 'accept':
            library, email, name = source.to_library, source.receiver_email, source.receiver_name
            active_id = None
        else:
            library, email, name = source.from_library, source.initiator_email, source.initiator_name
            active_id = None
        custody = db.scalar(select(SampleCustody).where(SampleCustody.batch_no == batch_no))
        if custody is None:
            db.add(SampleCustody(batch_no=batch_no, library=library,
                                 holder_email=email if email is not None else source.initiator_email,
                                 holder_name=name if name is not None else source.initiator_name,
                                 handover_id=active_id, updated_at=_now()))
            repaired += 1
        else:
            # email 为 None 表示在途单尚未转库，持有人保持原状。
            new_email = custody.holder_email if email is None else email
            new_name = custody.holder_name if name is None else name
            if (custody.library, custody.holder_email, custody.handover_id) != (library, new_email, active_id):
                custody.library = library
                custody.holder_email = new_email
                custody.holder_name = new_name
                custody.handover_id = active_id
                custody.updated_at = _now()
                repaired += 1
    if repaired:
        record(db, 'system', 'handover.recovered', {'repaired': repaired})
    cleanup_request_keys(db)
    db.commit()
    return {'active': len(active_by_batch), 'repaired': repaired}


def custody_of(db, batch_no):
    return db.scalar(select(SampleCustody).where(SampleCustody.batch_no == batch_no))


def handover_dict(order, user=None):
    data = {
        'id': order.id, 'order_no': order.order_no, 'batch_no': order.batch_no,
        'from_library': order.from_library, 'to_library': order.to_library,
        'sample_count': order.sample_count, 'note': order.note, 'status': order.status,
        'status_label': HANDOVER_STATUS_LABELS.get(order.status, order.status),
        'initiator': {'id': order.initiator_id, 'email': order.initiator_email, 'name': order.initiator_name},
        'receiver': {'id': order.receiver_id, 'email': order.receiver_email, 'name': order.receiver_name},
        'received_count': order.received_count, 'difference_note': order.difference_note,
        'withdraw_reason': order.withdraw_reason, 'resolution': order.resolution,
        'resolution_label': HANDOVER_RESOLUTION_LABELS.get(order.resolution, ''),
        'resolution_reason': order.resolution_reason,
        'resolved_by': order.resolved_by_name,
        'created_at': order.created_at, 'signed_at': order.signed_at,
        'withdrawn_at': order.withdrawn_at, 'disputed_at': order.disputed_at,
        'resolved_at': order.resolved_at,
    }
    if user is not None:
        data['can_sign'] = (order.status == 'in_transit' and order.receiver_id == user.id
                            and user.role in WRITE_ROLES)
        data['can_withdraw'] = order.status == 'in_transit' and order.initiator_id == user.id
        data['can_resolve'] = (order.status == 'dispute' and user.role == 'admin'
                               and user.id not in (order.initiator_id, order.receiver_id))
    return data


def handover_detail(db, order, user=None):
    data = handover_dict(order, user)
    data['events'] = [{'id': e.id, 'action': e.action, 'actor_email': e.actor_email,
                       'actor_name': e.actor_name, 'detail': e.detail, 'at': e.at}
                      for e in db.scalars(select(HandoverEvent).where(HandoverEvent.handover_id == order.id)
                                          .order_by(HandoverEvent.id))]
    return data
