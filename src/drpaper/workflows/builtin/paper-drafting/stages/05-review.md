---
stage: 审校评估
entry_tools: review_draft, finish_draft, evaluate_draft
---

## 阶段目标

整体审校并逐条修改，通过 finish_draft 结构化校验与 evaluate_draft 质量评估。

## 验收标准

- review_draft 返回的意见逐条处理：整节改动走 write_section 附 guidance（意见原文传入），小改直接 submit_section
- finish_draft 通过：各节齐全、总字数达标、引用与文献库一致、†单元格与估计结果一致、比较性断言已量化
- finish_draft 返回 citation_error / expected_value_error / unquantified_claims 时按 hint 修正后重新校验，通过前禁止导出
- evaluate_draft 完成：向用户报告总分与相对基线的差值；返回 hint 时按提示逐条改写并重新提交受影响节

## 可用工具

- review_draft：审校子代理整体审校，返回按严重程度分级的意见列表
- finish_draft：结构化校验，修改后可反复调用
- evaluate_draft：定量评估，结果保存为 eval.json
- remove_papers：移除未引用文献（编号会重排，需更新各节引用并重新提交受影响节）

## 进入下一阶段

校验与评估通过后，调用 export_docx 导出交付。
