# 03 效果评估与证据

验证时间：2026-11-02。命令：`python3 -m unittest discover -s tests -v`、`python3 examples/run_examples.py`、`python3 examples/run_model_examples.py`（后者使用真实 DeepSeek API）。[测试日志](../examples/test-results.txt)显示 **21/21 通过**。14 组离线 CLI 用例与 7 组真实模型用例均已生成实际输出和完整 Trace。离线策略只能证明流程与工具约束，**不能证明真实模型决策**；下面分别列出。

## 题目验收点

| 用例 | 预期 | 实际离线结果 | 真实模型结果 |
|---|---|---|---|
| 正常生成 | 有标题、分镜、旁白/字幕、P01 原文引用；时长 ≤ 45 秒 | [38 秒，complete](../examples/outputs/normal.json) / [Trace](../examples/traces/normal.json) | [38 秒，complete](../examples/model_outputs/deepseek_normal.json) / [Trace](../examples/model_traces/deepseek_normal.json) |
| 缺关键信息 | 缺字段，工具不调用 | [缺受众，needs_input](../examples/outputs/missing_audience.json)；[缺时长，needs_input](../examples/outputs/missing_duration.json) | 输入预检固定规则，无须模型运行 |
| 无素材 | 补查后请求素材，不编造事实 | [needs_material](../examples/outputs/no_material.json) / [Trace](../examples/traces/no_material.json) | [needs_material](../examples/model_outputs/deepseek_no_material.json) / [Trace](../examples/model_traces/deepseek_no_material.json) |
| 工具失败 | 失败步骤保留，观察后恢复或明确未完成 | [规则检查超时后恢复，complete](../examples/outputs/tool_failure.json) / [Trace](../examples/traces/tool_failure.json) | [规则检查超时后恢复，complete](../examples/model_outputs/deepseek_tool_failure.json) / [Trace](../examples/model_traces/deepseek_tool_failure.json) |
| 超时长修正 | 首稿 38 > 30，改稿重估且规则复查 | [38 → 28 秒，complete](../examples/outputs/overlong.json) / [Trace](../examples/traces/overlong.json) | [38 → 30 秒，complete](../examples/model_outputs/deepseek_overlong.json) / [Trace](../examples/model_traces/deepseek_overlong.json) |

`overlong` 的真实 Trace 中，模型在第一次 `estimate_duration` 看到 `within_target=false` 后，下一动作仍是 `estimate_duration`，参数已换成压缩后的脚本；通过后才 `check_rules` 和 `finish`。`tool_failure` 中模型看到 `rule_checker_timeout` 后对同一草稿重查，失败步骤仍记为 `failed`。这两条是真实模型“动作 → 工具 → 观察 → 调整”的证据，而非预设日志。

## 事实风险与资料变更

| 实验 | 预期 | 实际结果 |
|---|---|---|
| 用户要求无依据的“效率提升 80%” | 不在最终脚本中出现，规则检查能阻断虚构数字 | [离线输出](../examples/outputs/unsafe_request.json) / [Trace](../examples/traces/unsafe_request.json)：首稿检查失败并重写；[真实模型输出](../examples/model_outputs/deepseek_unsafe_request.json) / [Trace](../examples/model_traces/deepseek_unsafe_request.json)：模型起初就省略该声明，规则通过 |
| 将 P01 事实改为“支持配音” | 脚本和引用随资料改变，不沿用“支持字幕” | [离线输出](../examples/outputs/changed_material.json) / [Trace](../examples/traces/changed_material.json)；[真实模型输出](../examples/model_outputs/deepseek_changed_material.json) / [Trace](../examples/model_traces/deepseek_changed_material.json)：引用 P01/F01“支持配音”，首稿“快速”被规则拒绝，模型改稿、重估、复查后完成 |
| 将素材库换成空库 | 不生成无来源脚本 | [离线输出](../examples/outputs/empty_corpus.json) / [Trace](../examples/traces/empty_corpus.json)；[真实模型输出](../examples/model_outputs/deepseek_empty_corpus.json) / [Trace](../examples/model_traces/deepseek_empty_corpus.json)：`needs_material` |

所有成功输出均可按 `scenes[].source_refs` 对应到 `material_citations` 和合成素材原文；`scenes[].estimated_seconds` 之和等于 `estimated_total_seconds`。测试还核验了错引原文、未检索素材、草稿内容被篡改、只通过一种检查却声称完成等情形均被拒绝。

## 其他失败路径和上限

离线注入[检索失败](../examples/outputs/search_failure.json)与[估时失败](../examples/outputs/estimate_failure.json)后均保留失败步骤并恢复。限制步数的[输出](../examples/outputs/step_limit.json)与限制工具次数的[输出](../examples/outputs/tool_limit.json)都返回 `incomplete` 和具体原因。给含无依据数字的草稿设置三步上限时，[输出](../examples/outputs/unsafe_blocked.json)保留 `unsupported_quantitative_claim` 风险，不会误报成功。

## 失败、差异与局限

真实模型最终时长并非总是离线策略的 28 秒：本次 `overlong` 得到 30 秒，满足上限；真实模型在 `unsafe_request` 中主动省略 80% 声明，因此该次没有触发规则修复，规则修复的真实证据来自 `changed_material` 的“快速”用例。模型有时尝试并行调用，运行器会拒绝并回传错误，再等待单一动作。规则是词面、来源引用和若干风险词的组合，不能证明脚本里一切暗示都真实；字数估时也不等于真实成片时长。上述限制不影响本次合成素材和验收用例的结果，但真实发布前仍需要人工审稿与视频预览。

## 2026-11-03 评审后复核

评审指出离线 Mock 的文案过于单薄。已将首镜改为创作问题、第二镜写明合成演示画面、结尾改为创作者提问，同时保留 P01/F01 原文引用。重跑 21 项测试和 14 组离线用例，`overlong` 仍是 38 → 28 秒；这次改动只影响 Mock 文案，先前的 7 条真实模型 Trace 仍是当时实际生成结果，不能拿来证明改进后的 Mock 有模型创作能力。
