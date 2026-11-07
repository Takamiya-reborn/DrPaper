---
stage: 逐节写作
entry_tools: write_section, submit_section
---

## 阶段目标

按大纲顺序与各节预算产出全部章节正文。

## 验收标准

- 整节正文一律经 write_section 委托写作子代理（主对话禁止逐字撰写整节），一次只写一节，禁止一次传全文
- 每节字数在预算 85%~130% 内：返回 too_short/too_long 时按预算自行调整该节并重新提交，禁止反问用户
- 子代理返回 warning 时跟进处理（如引用编号超出文献库范围，先补检索或改引）
- 全部节写完且总字数达到目标前，禁止 finish_draft
- 修改已有草稿：整节改动走 write_section 附 guidance；一两句话的局部微调直接 submit_section 覆盖对应节；大改动重新 begin_draft 并重新走提纲确认

## 可用工具

- write_section：撰写/重写一节，guidance 传侧重要求或审校意见原文
- submit_section：仅限一两句话的局部微调
- estimate_metric：写含性能数字的对比表前必须先调用
- search_literature：写作中需要补充某方面文献时

## 注意事项

- 表格、数据、量化、公式纪律见系统提示词铁律；写作子代理的系统提示词已内置同一套纪律

## 进入下一阶段

全部节写完后调用 review_draft 审校。
