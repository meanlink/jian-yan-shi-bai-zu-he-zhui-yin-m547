import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, Handover, SampleCustody
from src.schemas.handover import HandoverInput, SignInput, WithdrawInput, ResolveInput
from src.services.business import metadata, seed_records, analyze
from src.services import handovers
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum

DEMO_USERS = [
    ('admin', '林知 · 业务负责人', 'admin@demo.local'),
    ('analyst', '陈舟 · 样品交出经办', 'analyst@demo.local'),
    ('analyst', '沈屏 · 样品接收员', 'receiver@demo.local'),
    ('viewer', '周禾 · 观察员', 'viewer@demo.local'),
]
# 演示批号与各自独立的复检事务切片，便于交接后照常发起分析。
DEMO_BATCHES = ['HP-R001 复检留样批', 'HP-R002 复检留样批', 'HP-R003 复检留样批', 'HP-R004 复检留样批']


def seed_handovers(db, rows):
    admin = db.scalar(select(User).where(User.email == 'admin@demo.local'))
    analyst = db.scalar(select(User).where(User.email == 'analyst@demo.local'))
    receiver = db.scalar(select(User).where(User.email == 'receiver@demo.local'))
    if not (admin and analyst and receiver):
        return
    for index, name in enumerate(DEMO_BATCHES):
        if not db.scalar(select(Dataset).where(Dataset.name == name)):
            chunk = rows[index * 40:(index + 1) * 40]
            db.add(Dataset(name=name, source=f'handover-batch-{index + 1}.json', records=chunk,
                           checksum=checksum(chunk), created_at=time.time()))
    db.flush()
    if db.scalar(select(Handover).where(Handover.batch_no.in_(DEMO_BATCHES))):
        return  # 交接演示数据只建立一次；后续状态由真实操作推进。

    # 1) 在途：交出经办发起，指定接收员尚未核对，签收入口直接可演示。
    handovers.initiate(db, HandoverInput(
        batch_no=DEMO_BATCHES[0], from_library='甲库 · 常温样品架', to_library='乙库 · 复核留样库',
        sample_count=12, receiver_id=receiver.id, note='复核留样整单转乙库，含留样标签与交接清单'),
        analyst)
    # 2) 争议：接收员核对发现少 1 支，整单转入争议，等待第三方负责人裁定。
    disputed = handovers.initiate(db, HandoverInput(
        batch_no=DEMO_BATCHES[1], from_library='甲库 · 常温样品架', to_library='乙库 · 复核留样库',
        sample_count=8, receiver_id=receiver.id, note='灰分复测留样 8 支'), analyst)
    handovers.sign(db, disputed.id, SignInput(
        received_count=7, difference_note='实收 7 支，清单第 6 项缺 1 支，包装封条完好'), receiver)
    # 3) 已签收：整单核对一致，保管归属转到丙库并留完整事件链。
    signed = handovers.initiate(db, HandoverInput(
        batch_no=DEMO_BATCHES[2], from_library='甲库 · 常温样品架', to_library='丙库 · 待检冷藏库',
        sample_count=6, receiver_id=receiver.id, note='黏度复测需冷藏留样'), analyst)
    handovers.sign(db, signed.id, SignInput(received_count=6), receiver)
    # 4) 已撤回：发起人在签收前撤回，样品归还原库，事件链保留撤回原因。
    withdrawn = handovers.initiate(db, HandoverInput(
        batch_no=DEMO_BATCHES[3], from_library='甲库 · 常温样品架', to_library='乙库 · 复核留样库',
        sample_count=5, receiver_id=receiver.id, note='色差留样预转库'), analyst)
    handovers.withdraw(db, withdrawn.id, WithdrawInput(reason='复检排期调整，暂不转库'), analyst)


def seed():
    migrate()
    with Session() as db:
        for role, name, email in DEMO_USERS:
            if not db.scalar(select(User).where(User.email == email)):
                db.add(User(email=email, name=name, role=role, password=hash_password('Demo123!')))
        db.flush()
        m = metadata()
        d = db.scalar(select(Dataset).where(Dataset.name == m['dataset_name']))
        if not d:
            rows = seed_records()
            d = Dataset(name=m['dataset_name'], source=m['short'] + '-202611.csv', records=rows,
                        checksum=checksum(rows), created_at=time.time())
            db.add(d)
            db.flush()
        if not db.scalar(select(Job).where(Job.name == '基线复核 · ' + m['name'])):
            params = {p['key']: p['default'] for p in m.get('parameters', [])}
            result = analyze(d.records, params)
            json.dumps(result, ensure_ascii=False, allow_nan=False)
            admin = db.scalar(select(User).where(User.role == 'admin'))
            j = Job(name='基线复核 · ' + m['name'], dataset_id=d.id, params=params, status='completed',
                    result=result, created_by=admin.id, created_at=time.time() - 3600,
                    finished_at=time.time() - 3595, approved=True)
            db.add(j)
            db.flush()
            r = Report(name='每日业务复核简报', job_id=j.id, destination='业务复核组 · 本地收件箱',
                       interval_seconds=86400, next_run=time.time() + 86400, enabled=True)
            db.add(r)
            db.flush()
            deliver(db, r)
            db.add(Audit(actor='seed', action='demo.baseline', detail={'dataset': d.id}, at=time.time()))
        seed_handovers(db, seed_records())
        db.commit()
        models = [User, Dataset, Job, Report, Delivery, Handover, SampleCustody, Audit]
        counts = {model.__tablename__: db.scalar(select(func.count()).select_from(model)) for model in models}
        print(json.dumps(counts, ensure_ascii=False))
        return counts


if __name__ == '__main__':
    seed()
