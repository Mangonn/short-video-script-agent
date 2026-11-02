# 04 AI 辅助使用与贡献记录

## AI 辅助范围

本项目的需求拆解、Python 实现、合成数据、测试、示例生成脚本和文档主要由 Codex 辅助完成；真实动作决策示例由 DeepSeek 模型通过函数调用产生。Codex 在检查第一次真实输出时发现“新手也能上手”和具体界面入口等表述缺少素材依据，随后扩充了规则与测试，再重新运行验证。DeepSeek 不负责最终事实或时长验收，程序工具负责硬性校验。

## 候选人个人贡献与未确认部分

候选人提供了四题统一交付规范、第四题完整题干、重点验收标准及目标 GitHub 仓库，并要求按标准反复核查。**当前没有可核实的候选人手写代码或人工修改记录**，因此不将 AI 生成的代码、文档或验证动作写成候选人个人实现。若候选人后续自行修改，应在这里追加实际文件、修改内容和验证命令；提交前应能独立解释方案与局限。

## 验证记录

- 本地运行 `python3 -m unittest discover -s tests -v`，结果保存在 `examples/test-results.txt`。
- 运行 `python3 examples/run_examples.py`，离线输出与 Trace 位于 `examples/outputs/`、`examples/traces/`；这是流程验证，不是模型决策证据。
- 使用真实 DeepSeek API 运行 `python3 examples/run_model_examples.py`；完成的输出与 Trace 位于 `examples/model_outputs/`、`examples/model_traces/`。模型密钥通过临时环境注入，未写入仓库。
- 评估的逐案例结论和实际差异见 `docs/03-evaluation.md`。

## 密钥与数据

`.env.example` 只有变量名及空值；`.gitignore` 排除 `.env`。仓库只有合成素材，没有真实用户信息。模型输出中保留动作、工具参数、工具观察和简短依据，不记录模型内部思维。
