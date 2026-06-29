# OA 事项能力矩阵

日期：2026-06-29

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

## 安全边界

- 发起侧：矩阵只给出 `oa launch dry-run` 和 `oa launch save-draft --confirm` 的下一步命令建议，不自动打开页面、不自动保存草稿。
- 接收侧：矩阵只给出 `oa matter preflight` 建议，不执行审批、归档、退回、删除、上传等动作。
- 模板未匹配时：矩阵标记为 `template_unmatched`，后续应先修正模板匹配或补采样。
- 会议类事项：会议回复已有单独治理路径，会议发起仍需单独建模，不能套普通协同模板发起执行器。

## 后续扩展

1. 对高频普通协同事项补发起页字段画像，优先验证保存草稿路径。
2. 对当前待办中出现的目标事项做 `matter preflight`，再决定是否提升接收处理动作。
3. 对会议发起、补签等特殊入口建立单独事项画像。
4. 每提升一个写动作，都把验证状态回填到矩阵，而不是只新增底层原子命令。
