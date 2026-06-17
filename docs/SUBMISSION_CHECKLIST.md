# CS599 Final Submission Checklist

截止时间：**2026 年 6 月 22 日 23:00**

## GitHub 仓库

- [ ] 仓库最终命名为 `cs599-project`。
- [ ] 如果仓库为 Private，添加 `qxr777` 为 Collaborator。
- [ ] 如果仓库为 Public，添加合适的开源 `LICENSE`。
- [ ] `.gitignore` 排除 `.env`、运行日志、缓存、临时 workspace 和敏感数据。
- [ ] 确认 Git 历史中不存在 API Key、QQ Bot Secret 等密钥。
- [ ] 对曾经提交过的密钥进行轮换。
- [ ] 提交记录能够体现 Proposal、MVP、Final 演进过程。

## README

- [x] 项目名称和一句话简介。
- [x] 明确方向一：Agentic AI 原生开发。
- [x] 技术栈。
- [x] 目录结构与模块职责。
- [x] 环境变量、依赖和启动步骤。
- [x] 项目状态。
- [x] Specs、架构图、评测和 Demo 文档入口。
- [ ] 补充外部代码、开源项目和论文引用。

## SDD Specs

- [x] Product Spec。
- [x] Architecture Spec。
- [x] API Spec。
- [ ] 根据最终代码复核所有接口和验收标准。

## 最终报告

- [ ] 在 `docs/report-source.md` 填写学号、姓名、专业。
- [ ] 补充 AI IDE 使用截图。
- [ ] 补充 Demo、trace、benchmark 和输出产物截图。
- [ ] 将最终 benchmark 结果更新到报告。
- [ ] 补充课程总结与个人反思。
- [ ] 生成 `docs/CS599_大作业报告.pdf`。
- [ ] 确认 PDF 具有可用导航窗格/书签。
- [ ] 检查封面字段完整，不得留空。

## 代码与安全

- [x] DashScope API Key 从环境变量读取。
- [x] QQ Bot App ID 与 Secret 从环境变量读取。
- [ ] 在 QQ 开放平台轮换曾经硬编码过的 Secret。
- [x] 核心 Python 文件通过 `py_compile`。
- [x] `python miniagent.py skills doctor`：`ERROR=0`。
- [ ] 运行完整 isolated benchmark。

