# 旧浏览器桥接退役记录

> 状态：退役一期已实施
> 日期：2026-07-13
> 基线提交：2d6a06b（退役前最后一个完整旧桥接版本）

## 1. 一期目标

旧 Chrome 扩展、浏览器轮询桥、localhost daemon、daemon 版 MCP 和代理型
CLI 不再是可运行路径。中心 CentralCapabilityService、每用户会话、
CentralBrowserWorker、可信卡片和中心 MCP 成为唯一公开运行架构。

本次删除运输层，不把以下独立业务资产当作桥接代码删除：

- seeyon_home.py 中的首页、模板、事项和详情解析；
- seeyon_write.py 中的写动作分类、计划和核验规则；
- seeyon_matter_catalog.py、seeyon_matter_intent.py 中的事项知识；
- seeyon_page_scripts/ 中已验证页面契约；
- api_discovery.py 和 discovered.py 中可供中心探索器复用的纯逻辑。

## 2. 已删除范围

- extension/；
- bscli/browser/bridge.py；
- bscli/daemon/；
- 旧 daemon MCP server；
- 旧 CommandRegistry/RuntimeEngine/TraceStore/tool manifest 运行框架；
- daemon、oa、explore、command、discovered 等代理型 CLI；
- 只验证扩展、daemon HTTP、轮询任务或代理 CLI 的测试。

旧配置中的 auth_mode=chrome_extension 在读取时迁移为
auth_mode=central_session。中心运行时不存在旧路径自动回退。

## 3. 已中心化能力

| 能力 | 中心状态 |
| --- | --- |
| 模板列表 | oa.template.list |
| 待办列表 | oa.workflow.pending.list |
| 已办列表 | oa.workflow.done.list |
| 跟踪列表 | oa.workflow.tracked.list |
| 流程详情 | oa.workflow.detail.get |
| 流程意见 | oa.workflow.opinions.list |
| 出差申请字段收集与计划冻结 | oa.business_trip.prepare |
| 出差申请保存待发 | oa.business_trip.save_draft |
| 补签申请字段收集与计划冻结 | oa.missed_punch.prepare |
| 补签申请保存待发 | oa.missed_punch.save_draft |
| 补签待办审批意见收集与计划冻结 | oa.missed_punch.approval.prepare |
| 补签待办审批通过 | oa.missed_punch.approve |
| 会议字段收集、会议室预检与计划冻结 | oa.meeting.create.prepare |
| 会议创建、发送与双重回读 | oa.meeting.create |

## 4. 尚待中心化的业务能力

这些能力不能恢复旧桥接入口。需要实现时，应按工作流建立新的中心能力，
复用每用户中心会话，并遵守统一治理模型。

| 能力族 | 退役前实现线索 | 后续目标 |
| --- | --- | --- |
| 其他待办审批提交 | 补签审批已中心化；其余流程仍需逐表单验证 ContinueSubmit、必填字段和回读规则 | 按工作流继续实现 prepare/authorize/commit/verify |
| 会议邀请回复 | 会议详情、参加/不参加/待定、回复后回读 | oa.meeting.reply.* 中心能力 |
| 其他申请单草稿 | 出差、补签已中心化；其余模板仍需独立 CAP4 字段契约与保存回读 | 每种申请单独立能力，不发布底层原子接口 |
| 事项矩阵 | 已发/已办/跟踪聚类、模板匹配、发起与接收处理覆盖度 | 基于中心列表离线分析并形成能力 backlog |
| 写动作探索 | endpoint candidates、launch inspection、preflight、promotion evidence | 中心内部 inspector，不对智能体暴露任意请求 |
| 附件、时间线和证据投影 | 详情页投影与附件元数据 | 扩展现有中心只读能力 |

退役前完整实现可从 Git 基线 2d6a06b 查看，但不得通过复制旧
daemon/extension 重新上线。迁移时只提取业务契约、样本和核验规则。

## 5. 退役验收

- CLI 帮助中不存在旧代理命令；
- 源码中不存在扩展运行时、轮询 bridge 或 localhost daemon server；
- 中心 CLI、MCP、认证卡和业务字段卡测试全部通过；
- 自动化测试保证旧入口不会重新出现；
- 真实 OA 验证必须在扩展未安装、daemon 未启动的条件下完成；
- 新能力不得新增对员工个人浏览器 Profile 的依赖。

## 6. 后续阶段

1. 继续按价值和频率迁移其他审批、高频申请单和会议邀请回复；
2. 为中心 Worker 增加受控内部探索器，替代旧 bridge 调试命令；
3. 每迁移一个能力，从本清单删除对应缺口并补充真实环境证据；
4. 完成第二用户隔离、真实手机网络和生产身份体系验证。
