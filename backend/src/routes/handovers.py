from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.constants.enums import LIBRARIES, HANDOVER_STATUS_LABELS
from src.middlewares.auth import current_user, can_write, admin_only
from src.models import Dataset, Handover, HandoverRequest, SampleCustody, User
from src.schemas.handover import HandoverInput, SignInput, WithdrawInput, ResolveInput
from src.services import handovers
from src.services.audit import record
from src.utils.serialization import custody_dict
router = APIRouter(prefix='/handovers')


def _idempotency_key(value: str | None):
    if value is not None and not 8 <= len(value) <= 120:
        raise HTTPException(422, 'Idempotency-Key 长度须在 8 到 120 字符之间')
    return value


@router.get('')
def listing(status: str = '', batch_no: str = '', db=Depends(get_db), u=Depends(current_user)):
    query = select(Handover).order_by(Handover.id.desc())
    if status:
        query = query.where(Handover.status == status)
    if batch_no:
        query = query.where(Handover.batch_no == batch_no)
    return [handovers.handover_dict(o, u) for o in db.scalars(query.limit(200))]


@router.get('/context')
def context(db=Depends(get_db), u=Depends(current_user)):
    receivers = [{'id': x.id, 'name': x.name, 'email': x.email, 'role': x.role}
                 for x in db.scalars(select(User).where(User.role == 'analyst').order_by(User.id))]
    return {
        'libraries': LIBRARIES,
        'receivers': receivers,
        'statuses': [{'value': k, 'label': v} for k, v in HANDOVER_STATUS_LABELS.items()],
        'custody': [custody_dict(c) for c in db.scalars(select(SampleCustody).order_by(SampleCustody.batch_no))],
    }


@router.get('/custody')
def custody(db=Depends(get_db), u=Depends(current_user)):
    return [custody_dict(c) for c in db.scalars(select(SampleCustody).order_by(SampleCustody.batch_no))]


@router.get('/trace/{batch_no}')
def trace(batch_no: str, db=Depends(get_db), u=Depends(current_user)):
    custody_row = handovers.custody_of(db, batch_no)
    orders = list(db.scalars(select(Handover).where(Handover.batch_no == batch_no).order_by(Handover.id.desc())))
    if not custody_row and not orders:
        raise HTTPException(404, '没有该批号的交接记录')
    active = next((o for o in orders if o.status in handovers.ACTIVE_STATUSES), None)
    return {
        'batch_no': batch_no,
        'custody': custody_dict(custody_row) if custody_row else None,
        'active': handovers.handover_dict(active, u) if active else None,
        'handovers': [handovers.handover_detail(db, o, u) for o in orders],
    }


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    order = db.get(Handover, id)
    if not order:
        raise HTTPException(404, '交接单不存在')
    return handovers.handover_detail(db, order, u)


@router.post('', status_code=201)
def initiate(data: HandoverInput, db=Depends(get_db), u=Depends(can_write),
             idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    key = _idempotency_key(idempotency_key)
    if key:
        cached = db.scalar(select(HandoverRequest).where(HandoverRequest.key == key))
        if cached:
            same_batch = cached.action == 'initiate' and cached.response.get('batch_no') == data.batch_no.strip()
            if not (cached.actor_email == u.email and same_batch):
                raise HTTPException(409, '该幂等键已被其他操作使用，请重新发起')
            return cached.response
    if not db.scalar(select(Dataset).where(Dataset.name == data.batch_no.strip())):
        raise HTTPException(404, '批号没有对应的已接入数据批次，请先接入数据')

    def operation():
        order = handovers.initiate(db, data, u)
        payload = handovers.handover_detail(db, order, u)
        return 201, payload, {'id': order.id, 'batch_no': order.batch_no,
                              'from_library': order.from_library, 'to_library': order.to_library,
                              'receiver': order.receiver_email}
    return handovers.perform(db, key, 'initiate', u, operation)[1]


@router.post('/{id}/sign')
def sign(id: int, data: SignInput, db=Depends(get_db), u=Depends(can_write),
         idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    key = _idempotency_key(idempotency_key)

    def operation():
        order = handovers.sign(db, id, data, u)
        return 200, handovers.handover_detail(db, order, u), {
            'id': order.id, 'batch_no': order.batch_no, 'status': order.status,
            'received_count': order.received_count}
    return handovers.perform(db, key, 'sign', u, operation, scope_id=id)[1]


@router.post('/{id}/withdraw')
def withdraw(id: int, data: WithdrawInput | None = None, db=Depends(get_db), u=Depends(can_write),
             idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    key = _idempotency_key(idempotency_key)
    data = data or WithdrawInput()

    def operation():
        order = handovers.withdraw(db, id, data, u)
        return 200, handovers.handover_detail(db, order, u), {
            'id': order.id, 'batch_no': order.batch_no, 'reason': order.withdraw_reason}
    return handovers.perform(db, key, 'withdraw', u, operation, scope_id=id)[1]


@router.post('/{id}/resolve')
def resolve(id: int, data: ResolveInput, db=Depends(get_db), u=Depends(admin_only),
            idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    key = _idempotency_key(idempotency_key)

    def operation():
        order = handovers.resolve(db, id, data, u)
        return 200, handovers.handover_detail(db, order, u), {
            'id': order.id, 'batch_no': order.batch_no, 'resolution': order.resolution,
            'reason': order.resolution_reason}
    return handovers.perform(db, key, 'resolve', u, operation, scope_id=id)[1]


@router.post('/recover')
def recover(db=Depends(get_db), u=Depends(admin_only)):
    result = handovers.recover(db)
    record(db, u.email, 'handover.recover', result)
    db.commit()
    return result
