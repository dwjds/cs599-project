# CS599 Course Deliverables

本目录保存 MiniAgent 作为《企业级应用软件设计与开发》期末大作业的交付材料。

源码主目录位于 `../src/`，课程提交时推荐仓库名使用 `cs599-project`。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [specs/product-spec.md](specs/product-spec.md) | Product Spec：问题、需求、验收标准 |
| [specs/architecture-spec.md](specs/architecture-spec.md) | Architecture Spec：架构、流程、状态与安全 |
| [specs/api-spec.md](specs/api-spec.md) | API Spec：消息、工具、校验、恢复与 trace 协议 |
| [evaluation.md](evaluation.md) | 测试方法、当前结果与失败分析 |
| [demo-script.md](demo-script.md) | 5 分钟现场演示脚本 |
| [report-source.md](report-source.md) | 最终课程报告 Markdown 源稿 |
| [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md) | 最终提交检查清单 |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | 完整系统架构图与 Runtime 时序图 |

## 最终需要生成

```text
docs/CS599_大作业报告.pdf
```

PDF 必须包含可用的导航目录/书签。生成前需要在 `report-source.md` 中填写封面个人信息，并补充 Demo、AI IDE 使用和关键代码截图。

## 生成 PDF

```powershell
D:\conda\envs\assistant\python.exe docs\build_report.py
```

构建器会根据 `report-source.md` 生成封面、可见目录、页码和三级 PDF 导航书签。修改源稿后重新运行即可更新 `docs/CS599_大作业报告.pdf`。
