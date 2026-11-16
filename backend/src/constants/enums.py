from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'

class HandoverStatus(str, Enum):
    in_transit = 'in_transit'
    signed = 'signed'
    withdrawn = 'withdrawn'
    dispute = 'dispute'
    resolved = 'resolved'

HANDOVER_STATUS_LABELS = {
    'in_transit': '在途',
    'signed': '已签收',
    'withdrawn': '已撤回',
    'dispute': '争议中',
    'resolved': '已裁定',
}

HANDOVER_RESOLUTION_LABELS = {'accept': '接收方留样', 'return': '退回交出方'}

# 样品保管库为业务主数据；演示环境使用固定标签，生产可由主数据接口提供。
LIBRARIES = ['甲库 · 常温样品架', '乙库 · 复核留样库', '丙库 · 待检冷藏库']
