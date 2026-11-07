---
stage: 导出交付
entry_tools: export_docx
---

## 阶段目标

导出规范排版的交付文件，并告知用户文件位置。

## 验收标准

- finish_draft 已通过后才调用 export_docx（引用校验不过会被阻止，先修正再导出）
- 导出成功后向用户报告各文件位置：docx、参考文献、PPT 大纲，以及 .tex（若导出成功）
- 用户明确要求不加批注时 include_guides=false

## 可用工具

- export_docx：导出 Word 文档、.tex 源码、参考文献与 PPT 大纲文件；不生成原稿文件
