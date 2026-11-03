# 短视频脚本 Agent（题目 4）

从合成产品素材生成可编辑的短视频脚本，并在交付前核对逐镜头时长、事实引用和品牌规则。运行方式为 **CLI**。真实模型模式使用 DeepSeek 函数调用；离线 `mock` 模式只验证流程和工具边界，**不证明真实模型的决策效果**。

## 环境与运行

- Python 3.11 或更高版本；只用标准库，无需 `pip install`。
- 在项目根目录运行以下命令：

```bash
python3 -m src.cli examples/inputs/normal.json --mode mock
python3 -m src.cli examples/inputs/overlong.json --mode mock
python3 -m src.cli examples/inputs/tool_failure.json --mode mock --fail-once check_rules
python3 -m unittest discover -s tests -v
python3 examples/run_examples.py
```

真实模型运行：把 `.env.example` 复制为 `.env`，填入自己的 `DEEPSEEK_API_KEY`（不要提交）；也可通过环境变量提供。`DEEPSEEK_MODEL` 默认 `deepseek-flash`。本地 HTTPS 证书若需指定，可在运行前设置 `SSL_CERT_FILE`，例如 macOS 的 `/etc/ssl/cert.pem`。

```bash
cp .env.example .env
python3 -m src.cli examples/inputs/overlong.json --mode deepseek
python3 examples/run_model_examples.py
```

命令将 JSON 结果写到标准输出；`--output path.json` 可保存结果。真实模型不可用时会返回 `model_unavailable`，不会暗中切换到 mock。`needs_input` 和 `needs_material` 是明确的待补充状态；`incomplete` 和输入错误的退出码为 2。

## 评审入口

| 内容 | 位置 |
|---|---|
| CLI、模型动作循环 | `src/cli.py`、`src/agent.py`、`src/models.py` |
| 素材检索、估时、规则核查 | `src/business.py` |
| 合成素材、规则 | `data/` |
| 可执行验收测试 | `tests/test_agent.py`、`examples/test-results.txt` |
| 输入、实际输出、逐步 Trace | `examples/inputs/`、`examples/outputs/`、`examples/traces/` |
| 真实 DeepSeek 输出与 Trace | `examples/model_outputs/`、`examples/model_traces/` |
| 需求、方案、评估、AI 使用、复盘 | `docs/01-requirements.md` 至 `docs/05-retrospective.md` |

核心用例：`overlong` 的首稿超时后重估；`unsafe_request` 检查并删除无依据的 80% 效果数据；`no_material` 补查后请求素材；`tool_failure` 注入一次规则工具超时并恢复。`changed_material` 将来源事实改成“支持配音”，验证引用和脚本随资料改变。每份 Trace 都包含动作、参数、工具返回、下一步动作和简要依据；不含模型内部思维。

输出中的 `task_status=complete` 表示脚本经同一版本的时长和规则检查通过；`material_citations` 给出素材 ID、事实 ID 和原文；`scenes` 给出分镜、旁白/字幕及实测估算的起止秒数。本项目没有发布功能。

## 范围与限制

素材和规则均为合成数据。估时采用字数速率与镜头最小时长的确定性近似，不能替代成片试听。规则检查能严格核对引用 ID/原文、未经引用的“支持…”产品能力、常见绝对化词及量化效果声明；自然语言的所有隐含事实无法由词面规则穷尽，因此真实上线仍需人工终审。真实模型行为有随机性；仓库内记录的是一次实际运行，不保证每次逐字相同。完整测试证据与局限见 `docs/03-evaluation.md`。

## 2026-11-03 评审后改进

改进离线 Mock 分镜文案，保留 38→28 秒修正与素材事实校验；Mock 仍只验证流程。见 [评估](docs/03-evaluation.md)。
