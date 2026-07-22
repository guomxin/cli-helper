# OA 事项能力矩阵

原始矩阵日期：2026-06-29
当前中心能力快照：2026-07-22

## 目标

`oa matter matrix` 把历史事项归类、模板匹配和写动作治理状态整理成一张只读能力表。它的用途不是执行 OA 写动作，而是帮助智能体先判断某个事项目前能做什么、还缺什么验证。

## 数据来源

- `oa history profile`：从已发、已办、跟踪等历史事项中聚类高频事项。
- `oa template list` / `template_list_api`：从模板中心 REST 读取可发起模板，包含 `template_id`、`form_app_id`、`category_name`、`module_type`、`body_type`。
- `oa template match`：把历史事项聚类匹配到可发起模板。
- `oa matter profile`：把匹配结果整理为事项目录。

`oa matter matrix` 只消费 `oa matter profile` 的结果，不打开模板发起页，不读取当前待办详情，不调度浏览器写任务。

## 命令

```bash
python -m bscli.cli.main --home .bscli oa matter matrix --kind all --limit 50
python -m bscli.cli.main --home .bscli oa matter matrix --kind all --keyword 报销 --fields matter_id,name,coverage_status
```

返回结构为 `bscli.oa_matter_matrix.v1`，核心字段包括：

- `coverage`：整体覆盖统计，例如可发起草稿的事项数量、模板未匹配数量。
- `items[].launch_handling`：发起处理能力，当前重点是是否具备 `launch_dry_run` 和 `launch_save_draft` 的安全路径。
- `items[].received_handling`：接收处理能力，当前统一表达为需要当前待办项后再走 `matter preflight`。
- `items[].coverage_status`：事项当前能力状态，例如 `launch_ready_received_preflight_ready` 或 `needs_template_match`。

## 当前中心能力快照

旧 `oa matter matrix` 是退役桥接时期的只读发现产物，继续作为模板匹配和
迁移线索，但不再是当前执行入口。当前智能体应以中心 Capability Registry
和 MCP 工具目录为准：共 30 个 OA 能力，其中 6 个只读、24 个受治理写阶段；
中心 MCP 总计 37 个工具。

| 事项 | 发起处理 | 接收处理 | 当前证据与限制 |
|---|---|---|---|
| 出差申请单 | `oa.business_trip.prepare` / `save_draft`；独立的 `submit.prepare` / `submit` | 暂无专用接收能力 | 草稿已做真实保存回读；2026-07-22 真实提交与撤销均成功，提交采用权威已发网格和详情双回读；正式提交要求独立 `oa:write:submit` |
| 请假申请单 | `oa.leave.prepare` / `save_draft`；独立的 `submit.prepare` / `submit` | 暂无专用接收能力 | 仅开放 `年休`、`事假`、`调休`；2026-07-22 真实提交与撤销均成功；正式提交要求独立 `oa:write:submit` |
| 补签申请单 | `oa.missed_punch.prepare` / `save_draft` | `oa.missed_punch.approval.prepare` / `approve` | 已形成独立的发起草稿和接收审批状态机；不暴露通用 `ContinueSubmit` |
| 新建会议 | `oa.meeting.create.prepare` / `create` | 当前中心端未开放会议邀请回复 | 已完成真实创建发送与会议室列表、详情双回读；会议室由实时空闲选项选择 |
| 效能数据流程 | 不适用 | `oa.efficiency_data.approval.prepare` / `approve` | 绑定标题族、自由协同字段结构、审批节点和流程身份；当前两条待办已完成零写页面探针，真实审批待用户逐条授权 |
| 差旅费审批报销单 | 暂未开放发起 | `oa.travel_expense.approval.prepare` / `approve` | 固定模板 `-2046021869351779722`、表单 `-2571419096251022663`；授权卡展示金额、关键归属字段和附件数，不展示收款账号；真实审批待用户授权 |
| 周报发送流程 | 人工发起入口暂未开放 | `oa.weekly_report.acknowledgement.prepare` / `acknowledge` | 固定模板 `1610567580409022440`、表单 `-2351708227632217917`；OA 节点是“知会”而非审批，提交时不伪造同意态度；真实阅办待用户授权 |
| 普通协同事项 | 暂未开放发起 | `oa.standard_collaboration.approval.prepare` / `approve` | 仅允许无模板、无专业表单字段的普通协同，显式排除 HR、报销、采购、用印、效能数据和周报标题族；当前通知事项已完成零写页面探针 |
| 已发流程撤销（跨事项） | `oa.workflow.revoke.prepare` / `revoke` | 不适用 | 独立 `oa:write:revoke`；仅接收已发列表返回的精确 `affair_id`，强制填写撤销附言和单独授权，以已发消失且同一事项回到待发撤销态为成功标准；2026-07-22 已完成出差与请假测试流程的真实撤销闭环 |
| 其他历史事项 | 仅保留模板/历史发现证据 | 仅保留只读预检证据 | 未形成工作流专用中心能力前不得借用相似表单的底层提交动作 |

这里的 `prepare`、`save_draft`、`submit`、`approve` 和 `create` 是面向
智能体的业务能力阶段，不是让智能体直接拼接 OA API。每个事项可以拥有不同
的底层接口、字段和成功判据；能力矩阵只表达可用边界，不把原子动作对外暴露。

## 安全边界

- 发起侧：矩阵只给出 `oa launch dry-run` 和 `oa launch save-draft --confirm` 的下一步命令建议，不自动打开页面、不自动保存草稿。
- 接收侧：已注册事项可走“字段卡 → 精确事项授权 → 单次处理 → 待办消失回读”；每次授权只绑定一个 `affair_id`。未注册事项仍只允许只读预检，不得借用其他流程的提交动作。
- 模板未匹配时：矩阵标记为 `template_unmatched`，后续应先修正模板匹配或补采样。
- 会议类事项：会议回复已有单独治理路径，会议发起已有 direct-create
  CLI/daemon 路径，但仍不能套普通协同模板发起执行器；矩阵应把它标成特殊模块能力。
- 已发撤销：是跨事项的独立受治理能力，不属于某个表单的发起或接收原子动作，也不得自动用于测试数据清理。OA 可能产生通知、审计和表单业务副作用。

## 后续扩展

1. 对高频普通协同事项补发起页字段画像，优先验证保存草稿路径。
2. 对当前待办中出现的目标事项做 `matter preflight`，再决定是否提升接收处理动作。
3. 将会议发起的 direct-create 验证状态回填到特殊事项画像，并继续为补签等特殊入口建立画像。
4. 每提升一个写动作，都把验证状态回填到矩阵，而不是只新增底层原子命令。
