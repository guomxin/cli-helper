# 照明实验室测试系统二期能力包

## 一、目标

一期解决了“能登录、能看概览、能查基础列表”的问题。二期把能力从页面级读取推进到
可直接办事的业务查询：智能体能够先定位资产，再读取详情；能够对告警、巡检和漏电
记录做有界分析，而不是把一页原始表格直接交给用户。

二期继续保持只读。以下能力不在本期范围内：远程开关灯、回路控制、参数配置、告警
处置、巡检打卡、设施新增修改删除及其他会改变下游状态的操作。

## 二、能力分组

### 2.1 二期 A：详情与分析

| MCP 工具 | AgentBridge capability | 业务用途 | 状态 |
| --- | --- | --- | --- |
| `smartlight_asset_search` | `smartlight.asset.search` | 统一查询控制柜、RTU 和灯杆 | 已发布验收 |
| `smartlight_asset_detail` | `smartlight.asset.detail` | 读取单个设施详情；RTU 同时返回继电器和回路 | 已发布验收 |
| `smartlight_alarm_analysis` | `smartlight.alarm.analysis` | 按时间、状态和关键词分析 RTU 告警 | 已发布验收 |
| `smartlight_inspection_task_detail` | `smartlight.inspection_task.detail` | 查询巡检任务每日进度和实际打卡记录 | 已发布验收 |
| `smartlight_leakage_analysis` | `smartlight.leakage.analysis` | 分析漏电趋势、集中灯杆、道路、类型和状态 | 已发布验收 |

一期的五个读取工具继续保留，避免已有智能体提示词和客户端失效。统一资产查询是新增
入口，不会删除 `smartlight_lamppost_list`。

### 2.2 二期 B：报告导出

增加 `smartlight_report_export`，把照明只读查询生成 CSV 文件并通过 AgentBridge 任务附件
交付。工具继续只要求 `smartlight:read`，不会执行下游写入。首批支持四类报告：

| `report_type` | 报告内容 | 必要条件 |
| --- | --- | --- |
| `alarm_analysis` | RTU 告警明细 | 可选日期、状态、关键词和告警类型 |
| `leakage_analysis` | 漏电记录明细 | 可选日期范围或最近 N 天 |
| `asset_inventory` | 控制柜、RTU 或灯杆清单 | 必须指定 `asset_type` |
| `inspection_progress` | 巡检每日进度；指定日期时导出当天打卡明细 | 必须指定 `task_id` |

单份报告最多导出 500 行，并返回下游总数、实际导出行数和 `truncated`。CSV 使用
UTF-8 BOM，便于中文 Windows/Excel 直接打开；所有文本单元格都进行公式注入防护。

报告文件按用户、系统会话和任务绑定。下载链接 30 分钟有效，过期后历史卡继续保留并
显示“已过期”，用户可在 Workspace 重新生成。重新生成会使用原查询条件重新读取当前
数据，而不是恢复一份已经删除的历史缓存快照，因此生成时间不同的数据可能发生变化。

## 三、工具契约

### 3.1 统一资产查询

`smartlight_asset_search` 输入：

| 字段 | 含义 |
| --- | --- |
| `asset_type` | 必填，`cabinet`、`rtu` 或 `lamppost` |
| `keyword` | 可选，名称、编号等页面支持的模糊条件 |
| `page` / `size` | 分页；单页最多 100 条 |

返回统一的 `id`、`code`、`name`、位置、归属和状态字段，同时保留各设施类型特有的
少量业务字段。智能体必须先通过查询取得 `asset_id`，不要求用户记忆内部 ID。

`smartlight_asset_detail` 输入 `asset_type` 和 `asset_id`：

- 控制柜：返回容量、供电类型、道路、工区、地址和坐标等登记信息。
- RTU：返回型号、控制柜、分组、运行状态等信息，并列出继电器、工作模式和关联回路。
- 灯杆：返回灯杆类型、高度、灯具数量、道路、控制柜、RTU、工区和坐标等信息。

找不到目标时返回 `found=false`，不会用相似资产冒充精确结果。

### 3.2 RTU 告警分析

`smartlight_alarm_analysis` 支持：

- `start_date` / `end_date`，或互斥的 `last_days`；未提供时默认最近 30 天。
- `time_field=last_activity|occurred`，分别表示按末次发生时间或首次发生时间过滤。
- `alarm_state=all|current|cleared`。
- `keyword`、`alarm_type` 和 `top_n`。

返回日期范围、下游命中总数、本次实际分析数、是否截断、当前/已消除状态分布、告警
类型排行、设备排行、日期趋势和最近记录。单次最多分析 500 条，超过时明确
`truncated=true`，不得把样本统计表述为完整总体统计。

### 3.3 巡检任务详情

`smartlight_inspection_task_detail` 以一期列表返回的 `task_id` 为入口：

- 默认返回任务每日的计划设备数、实际完成数和下游给出的完成率。
- 提供 `detail_date` 时，返回该日已产生的巡检打卡记录，可再按打卡人和是否发现问题
  筛选。
- 支持 `start_date` / `end_date` 限定每日进度范围。

目标系统当前没有提供完整的“未巡设备明细”接口，因此本工具只能陈述计划数、完成数、
每日进度和实际观察到的打卡/异常记录，不能根据差值编造未巡设备清单。

### 3.4 漏电分析

`smartlight_leakage_analysis` 支持显式日期范围或 `last_days`，默认最近 30 天。返回：

- 日期趋势；
- 灯杆、道路、告警类型和状态排行；
- 最近记录；
- 下游总数、实际分析数和截断标记。

统计只基于日期范围内实际取回的记录。系统首页的全局实时计数仍由一期
`smartlight_leakage_summary` 单独返回，不混入范围分析。

## 四、已验证的下游接口

| 业务 | 只读接口 |
| --- | --- |
| 控制柜查询 | `rControlCabinet/getDataByCondition` |
| RTU 查询 | `rRtu/getDataByCondition` |
| RTU 继电器/回路 | `rRturelay/getDataByCondition` |
| 灯杆详情 | `lLamppost/getLampPostDetail` |
| RTU 告警 | `rHisHitchAlarm/getDataByRtuAlarm` |
| 巡检每日进度 | `InspectionDeviceGroup/getDataByCondition` |
| 巡检打卡记录 | `inspectionTask/getClockinDataByTaskId` |
| 漏电记录 | `lHisHitchAlarm/getDataByCondition` |

这些接口均通过中心端保存的 CAS/JWT 会话调用。Cookie、JWT、密码摘要、组织角色 ID
等传输细节不会作为工具参数暴露给智能体。

## 五、调用编排

```mermaid
flowchart LR
    U["用户业务问题"] --> A["智能体选择二期工具"]
    A --> S["资产查询或范围分析"]
    S --> B["AgentBridge 检查身份与 smartlight:read"]
    B --> C["复用中心 CAS/JWT 会话"]
    C --> D["调用已验证只读接口"]
    D --> E["标准化、限量、聚合"]
    E --> F["结构化结果返回各客户端"]
```

资产详情推荐采用两步调用：先按用户能理解的名称或编号搜索，再以返回的精确 ID 读取
详情。这样既降低误命中，也不会把下游内部 ID 暴露为用户必须掌握的知识。

## 六、安全与性能边界

1. 所有新增工具只要求已有 `smartlight:read`，不增加写权限。
2. 分页查询单页最多 100 条；分析最多读取 500 条。
3. 分析结果必须返回 `analyzedCount`、`downstreamTotal` 和 `truncated`。
4. 日期按 `Asia/Shanghai` 计算闭区间。
5. 登录失效时沿用可信验证码登录卡，并在登录后自动续办原任务。
6. 任何下游字段缺失都返回空值，不从名称、数量差或相邻记录推断事实。
7. CSV 报告最多 500 行；超出时必须标注截断，不把有界样本称为完整数据。
8. 报告下载只允许同一 `userSubject` 获取；过期重新生成仍需对应照明会话有效。
9. 重新生成报告按原条件读取当前数据，界面和工具结果必须明确这一语义。

## 七、验收标准

1. 第一用户可在 Workspace、Telegram 和微信使用新增工具；未授权用户看不到工具。
2. 三类资产均可完成“名称查询 -> 精确详情”闭环。
3. RTU 详情至少能返回一条真实继电器/回路结构；空数据也必须给出明确空列表。
4. 告警分析的时间口径、状态筛选、总数和截断标记与页面语义一致。
5. 巡检详情能返回每日进度；指定有数据日期时能返回真实打卡记录。
6. 漏电分析的趋势与排行仅使用指定日期范围内的数据。
7. 工具目录、OpenClaw 插件目录和服务器运行版本一致。
8. 单元测试、MCP 合约测试、OpenClaw 测试和真实只读冒烟测试全部通过。
9. 工具目录中仍不存在照明控制或业务写入能力。
10. 四类 CSV 报告可下载，中文内容可直接打开，过期历史卡可原位重新生成。

## 八、实施顺序

1. 交付二期 A 的五项工具及单元测试。
2. 在中心服务器部署，完成第一用户真实只读验收。
3. 观察一段实际问法，修正字段命名和智能体工具说明。
4. 扩展通用报告附件契约，再交付二期 B 的 CSV 报告导出。

二期 A 的真实验收记录见
[照明实验室测试系统二期 A 验收报告](../../acceptance/smartlight/smartlight-phase2-acceptance-2026-08-12.md)。
二期 B 的 CSV 文件交付验收记录见
[照明实验室测试系统二期 B 验收报告](../../acceptance/smartlight/smartlight-phase2b-acceptance-2026-08-12.md)。
