# ShopLens 开发导航

- 先读 `README.md` 了解当前可运行范围和本机命令；`planning/PROJECT_PLAN.md` 定义产品、指标和验收边界，`planning/IMPLEMENTATION_PROMPTS.md` 给出分阶段任务。`docs/progress.md` 的**最新阶段记录**是实际完成与未验证事项的依据；旧阶段的交接描述可能已过时。
- 数据口径以 `data/metrics.yaml` 和计划为准；实现与手算样例在 `data/`，数据库迁移在 `backend/migrations/`，测试在 `tests/`，环境决策在 `docs/decisions.md`。
- 只使用固定种子的**合成演示数据**。金额以整数分处理，业务日期按 Asia/Shanghai 的左闭右开区间解释；保留跨期退款、跨类别订单、多次退款和零分母等边界样例。参考答案不得进入模型上下文。
- 凭据只从环境或被忽略的 `.env` 读取，不写入源码、日志或截图。模型 SQL 只能经受限查询流程使用分析视图和 reader 凭据；不能用模拟数据库测试代替真实 PostgreSQL 权限/时区验证。
- 按当前阶段改动，不把后续阶段标为已完成。每次交接在 `docs/progress.md` 写实际命令、结果、未执行项和限制；不要把离线 stub 当真实模型验证。
- 统一验收：`.\.venv\Scripts\python.exe scripts/verify.py`；真实模型需显式加 `--real-model`。PostgreSQL 暂不可用时可用 `--skip-postgres` 跑其余项目，但输出必须保留该项“未执行”。
- 每完成一个阶段：先完成阶段验收并更新 `docs/progress.md`，检查待提交文件不含密钥、本地数据或构建产物，再提交并推送到 ShopLens 的 GitHub 仓库；记录提交号和推送结果。未验证通过或远端未确认时，不写“已推送”。
