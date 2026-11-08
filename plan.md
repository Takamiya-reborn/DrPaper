# DrPaper Agent 改进计划（参考 Claude Code 设计）🚧

> 目标：解决自然语言处理生硬（意图解析层）和 workflow 处理生硬（固定 pipeline）两大问题。
> 按顺序完成，每步完成后勾选，验证项全部通过才算该步完成。

## P0-1 移除关键词解析层，切换到 function calling

- [x] 定位所有 `if "xx" in user_input` / 正则意图匹配 / 意图分类器的代码，列出清单
      （唯一残留：core.py 的 `_TARGET_PATTERN` 目标字数正则 + CLI 依赖它的 `last_target_detected` 提示；其余正则均为文档/数据处理，非意图解析）
- [x] 将现有"命令"全部改写为工具定义（JSON Schema），逐个补全 description：- 参数格式、何时该传、反例 - 工具之间的边界与互斥关系 - 中文用户的真实说法作为触发示例（如"生成初稿""写正文"→ 对应工具）
      （新增 set_target_chars 工具：N 万换算、起草附带字数须先设定、局部篇幅反例）
- [x] 用户输入直接连同全部工具定义交给 LLM，由模型返回 tool_use
- [x] 删除旧解析层代码（\_sync_target / \_TARGET_PATTERN / last_target_detected 已删）
- [x] 验证：用 10 条口语化中文指令（含错别字、缩写、多意图混合）测试，全部路由到正确工具
      （scripts/verify_p0_routing.py，真实 LLM 两轮连续 10/10；含局部篇幅反例）

## P0-2 用 Agent Loop 替代固定 pipeline 的失败分支

- [x] 将主流程改为标准 agent loop：
      收集上下文 → 模型返回 tool_calls → 执行 → 结果回填 → 循环，直到模型不再调用工具
      （client.py chat() 原本已是该结构，本轮验证并补测试覆盖）
- [x] 删除编排代码中的重试/回退逻辑，失败时把错误信息原样作为 tool_result 喂回模型，
      由模型自行修正（代码里只保留工具执行本身的异常捕获）
      （删除 ppt_outline_writer 静默回退大纲、metrics 抽取失败退化为空两处回退分支；
      工具异常统一由 client.\_run_handler 捕获回传；网络层瞬时错误重试保留）
- [x] 设置最大循环轮数（如 25 轮）作为兜底，超限时把当前状态和原因返回用户
      （client.py max_rounds=40，超限返回进度提示与「继续」接续指引）
- [x] 验证：人为制造一次工具失败（如文件路径错误），agent 能自行纠正并完成任务
      （tests/test_p0_agent_loop.py：错误 tool_result 回传历史 → 模型改参重交 → 成功；
      另有轮次上限、未知文件错误、PPT 无静默回退、抽取失败上抛共 8 项）

## P1-1 意图模糊时的澄清与确认 gate

- [x] 工具 description 中声明：必填信息缺失时，模型应先向用户追问而不是猜参数
      （系统提示词新增「澄清优先」铁律：一次问全，禁止猜参数/编造题目；信息足够时不得反复追问）
- [x] "写作前先出提纲 → 用户确认 → 再动笔"做成硬性流程节点（类似 Claude Code 的 plan mode）
      （新增 confirm_outline 工具：begin_draft 后提纲待确认，结构性校验保证用户未回应时模型不得自行确认；
      未确认前 submit_section 直接拒绝；用户明确要求跳过确认时可放行；重新 begin_draft 重置确认状态）
- [x] 验证：输入"帮我写篇论文"这类宽泛指令时，agent 先追问研究方向/要求，而不是直接开写
      （tests/test_p1_clarify_gate.py 5 项门禁机制测试；
      scripts/verify_p1_clarify.py 真实 LLM 三场景 3/3：宽泛指令先追问、提纲提交后停在确认节点、
      确认后 confirm_outline 先于 submit_section）

## P1-2 章节写作拆分为 subagent（按类别分层组织代码）

- [x] 新建子代理模块，按类别分层：`agents/research/`、`agents/writer/`、`agents/reviewer/`
      （`agents/base.py` 骨架：独立消息历史 + 独立工具集 + 独立轮次上限；结构化产物由 submit 类工具处理器捕获，不走最终回复）
- [x] 每个子代理是独立的完整 agent loop，拥有独立上下文
      （调研复用主循环检索 handler 共享文献库；写作子代理预算自纠正在自身 loop 内完成，正文经 save_section 直写共享 Draft）
- [x] 子代理只向主循环返回结构化摘要（调研返回资料卡，写作返回章节文本，审校返回意见列表）
      （写作交回 digest 不交正文；审校交回分级意见列表；系统提示词对每节完全一致保证风格统一）
- [x] 主循环只持有提纲 + 各章摘要，不持有子代理的中间过程
      （tools.py 新增 research_topic / write_section / review_draft 与 research_notes / section_digests / subagent_usage 状态；
      prompts.py 起草角色改为总控 + 分工纪律铁律；审校/评估/导出等后续阶段的小幅直改仍允许 submit_section，单次超 300 字视为整节重写）
- [x] 验证：写一篇 5 章论文，主上下文 Token 消耗相比拆分前明显下降，且各章风格一致
      （tests/test_p1_subagents.py 7 项 + 既有套件 26/26；scripts/verify_p1_subagents.py 真实 LLM 5/5，
      12,000 字规模对比口径为「纯写作 turn 内主上下文增量」——排除 turn 起点固定开销，只计随正文线性增长的部分：
      旧路径（逐节 submit_section）+8,247 → 新路径（write_section 委托）+1,560 tokens，下降 81%，判定线 <70%；
      结构性保证同轮验证：整节正文零泄漏进主对话、7 节 digest 齐全、审校返回分级意见列表；
      配套 client.py chat() 新增错误熔断：同一工具重复相同错误达 3 次停止执行本轮剩余调用并注入纠偏提示，
      重蹈覆辙超 2 次终止任务——验证中曾出现模型跳过 confirm_outline 后 153 次重试写节的死循环，熔断封顶此类损失）

## P1-3 Workflow 文档化（Skills 式）

- [x] 将每个 workflow（如"选题→初稿"）写成 Markdown 流程文档：阶段划分、
      每阶段验收标准、可用工具、注意事项
      （`workflows/builtin/paper-drafting/`：WORKFLOW.md 总则 + stages/ 六个阶段文档
      01-需求确认 → 06-导出交付；原系统提示词的固定流程步骤 1-6 全部迁入，
      prompts.py 只保留阶段链索引与全局铁律）
- [x] 运行时按需注入当前阶段的流程文档（progressive disclosure），不整篇塞入
      （ToolBox 统一包裹工具处理器：模型调用某阶段入口工具（frontmatter entry_tools）
      即视为进入该阶段，首次进入时该阶段文档随工具结果注入一次；
      阶段只前进不回退，同阶段重入/修订不重复灌历史；用户目录 ~/.drpaper/workflows
      可同名覆盖内置版，与 skills 同款两级加载）
- [x] 改流程只改 Markdown，不改代码
      （阶段顺序=阶段文档文件名序号；增删阶段、调整入口工具、改验收标准全是文档操作）
- [x] 验证：调整某阶段的验收标准后，不改任何 Python 代码即可生效
      （tests/test_p1_workflow_docs.py 7 项 + 既有套件 33/33；
      scripts/verify_p1_workflow.py 真实 LLM 3/3：阶段文档随工具结果注入且模型停在
      提纲确认节点、复制内置文档仅给「提纲与确认」阶段追加一条验收标准后真实模型照做
      （回复以【提纲待确认】开头，零代码改动）、系统提示词已无内嵌流程仅含阶段链索引）

## P1-4 任务清单持久化与断点续跑

- [x] 实现 TodoList 持久化：步骤状态（pending / in_progress / completed）存磁盘（JSON）
      （agent/todo.py 清单模型 + agent/session.py 快照落盘 output_dir/session.json；
      ToolBox.snapshot() 组装清单+草稿+文献库+确认门禁等全部可恢复状态）
- [x] 模型可通过工具读写该清单；每完成一步立即更新
      （read_todo / update_todo 两个工具；update_todo 整体替换并校验三种状态；
      handlers 统一包裹：每次工具调用成功后快照即时落盘，子代理内部调用不触发）
- [x] 支持 agent 中断后从清单恢复继续
      （PaperAgent 启动时读快照恢复 ToolBox 状态，系统提示词追加「断点恢复」块：
      清单复选框视图 + 草稿进度 + 禁止重做已完成步骤；CLI 启动时给出接续提示；
      快照损坏/缺失/清单全部完成时不注入，正常重新开始）
- [x] 验证：任务进行到一半杀掉进程，重启后 agent 能读取清单继续，不重做已完成步骤
      （tests/test_p1_todo_resume.py 9 项 + 既有套件 41/41；
      scripts/verify_p1_todo_resume.py 真实 LLM 4/4：清单随 update_todo 即时落盘、
      第 1 节完成后模拟进程被杀、重启恢复草稿与清单（第 1 节 534 字逐字一致、
      提纲确认门禁一并恢复）、「继续」后直接续写剩余节并导出 docx——
      无 begin_draft 复现、接手阶段未重做第 1 节，全程仅 1 次引言修订且为审校驱动）

## P2-1 上下文压缩（compaction）

- [x] 监控上下文长度，超过阈值（如 70% 窗口）时触发一次摘要调用，替换历史
      （agent/compaction.py 无状态 Compactor：每轮请求前用实测输入 token 判断占用
      是否超窗口 ×0.7（无实测则按消息体量估算，中英混排 ≈2 字符/token）；
      触发即取「保留尾 6 条之前」为折叠区，折叠边界只落在 assistant.tool_calls 与
      tool 结果对之间，永不拆对；旧历史整体替换为单条用户消息 =「压缩摘要」头 +
      LLM 摘要 + 权威状态快照；client.chat() 每轮把实测 prompt_tokens 与 compactor
      传给 Compactor.before_round，主循环与三个子代理共享同一 Compactor 实例）
- [x] 摘要必须保留：已确定的关键决策、未完成事项、用户明确的要求
      （摘要系统提示词规定保留顺序：关键决策/用户要求原话/未完成事项/关键工具结果；
      权威状态为机械注入的结构化快照（清单复选框、目标字数、提纲已确认标记、
      大纲节名与字数预算、文献库编号对照），与摘要冲突时以快照为准——
      不依赖模型转述；提纲确认门禁改为「待用户回复」标志位 + chat() 开头
      note_user_turn() 记录真实用户输入，压缩折叠历史后门禁依然正确）
- [x] 验证：超长任务中压缩后，agent 不遗忘已确认的提纲和写作要求
      （tests/test_p2_compaction.py 11 项 + 既有套件 55/55；
      scripts/verify_p2_compaction.py 真实 LLM 3/3：压低触发线后续写轮开头即触发
      折叠（全程 3 次，均为写作推进后再越限的合法折叠），折叠消息 3519 字且
      权威状态/提纲已确认/写作要求原话「自称用本文」/全部节名俱在；
      压缩后未重新 begin_draft、未重新请求确认，题目与提纲全程一致，7/7 节成稿
      并导出 docx）

## P2-2 清理与回归

- [x] 删除所有被新机制取代的死代码
      （AST 引用扫描后删除三处：draft.py 冗余 section_chars 字段、
      claims.py 未用的 finding_dict 与 asdict 导入、literature/aggregator.py
      从未实现的 _Provider Protocol；runtime.py 的 workspace_dir 基础设施
      经用户确认刻意保留）
- [x] 全流程回归：从选题到导出 docx / PPT 大纲各跑一遍
      （tests/test_p2_regression.py 3 项离线全流程 + 既有套件 55/55；
      scripts/verify_p2_regression.py 真实 LLM 3/3：选题→提纲→确认→逐节写作
      →审校→收尾→导出全流程走通，题目「融合检索增强与不确定性校准的大语言
      模型幻觉缓解框架」7 节全部成稿，docx/references.json/ppt_outline.json/
      ppt_outline.md 齐全；PPT 大纲 12 页字段完整无署名字段；导出文件无作者
      元数据（docx 核心属性 author/last_modified_by/title 为空，LaTeX 无 \author））
- [ ] 验证：导出文件无作者元数据（沿用既有规则）
