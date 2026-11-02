# 01 需求与成功标准

## 用户和目标

创作者输入主题、受众、平台、目标时长和风格。Agent 读取产品素材后输出可编辑的标题、逐镜头画面与旁白/字幕、预计时长和可追溯素材引用。事实缺口或工具问题要明确告知。

## 输入、工具与输出

- 输入：`topic`、`audience`、`platform`、`target_duration_seconds`、`style`，JSON 示例见 `examples/inputs/`。
- 数据：`data/materials.json` 中 P01 只有 F01“支持字幕”；`data/changed_materials.json` 改为“支持配音”；`data/empty_materials.json` 用于检索为空。`data/brand_rules.json` 定义平台上限、估时参数、绝对化和无依据功能词。
- 工具：`search_materials(query)`、`estimate_duration(script)`、`check_rules(script_id)`。CLI 的 `--fail-once` 注入一次工具失败。
- 输出：标题、分镜、每镜头估时、总时长、素材 ID 与原文引用、风险、资料缺口、步骤状态、工具结果和任务状态。

## 成功与失败边界

完成条件：至少一条从已检索素材取得的有效事实引用；同一 `script_id` 的估时不超过目标，且规则检查通过。缺关键输入返回 `needs_input`；两次检索仍无素材返回 `needs_material`；达到动作/工具上限或无法满足条件返回 `incomplete`，写明原因。模型不能直接宣布成功绕过检查。

## 验收映射

| 题目验收点 | 可运行用例 |
|---|---|
| 正常生成 | `normal` |
| 缺关键信息 | `missing_audience`、`missing_duration` |
| 无素材 | `no_material`、`empty_corpus` |
| 工具失败 | `tool_failure`、`search_failure`、`estimate_failure` |
| 超时长修正或明确失败 | `overlong`、`step_limit`、`tool_limit` |
| 事实追溯及改变素材后的动作 | `unsafe_request`、`changed_material` |

界面、部署、多 Agent 和接真实视频发布均不在范围内。
