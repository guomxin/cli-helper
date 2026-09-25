---
name: oa-pending
description: 办理已支持类型的 OA 待办，核对依据后通过可信卡单条或逐项处理；只读查看待办无需加载本 Skill。
---

# OA 待办办理

仅查看、筛选或统计待办时直接调用 oa_workflow_pending_list，不加载本 Skill。用户要办理、批量办理或取消办理任务时才使用以下流程。

1. 先查询待办并确认精确目标；同名和模糊指代需消歧。按实际支持类型及当前用户权限选择准备入口。

2. 单条使用该类型 prepare；多项/全部使用一次 oa_workflow_pending_batch_prepare，传实际读到的 affair_ids 或支持的筛选。

3. 不循环单条 prepare 冒充持久批量；超限、不支持类型或权限不足时明确说明，不静默截断。

4. 业务详情与意见填写由可信卡展示；不替用户决定批准或拒绝。每项独立授权，中央核验成功后推进。

5. 取消使用真实 task_id 的 agentbridge_task_cancel；提交后不能靠取消任务撤回业务。结果未知不自动重试。
