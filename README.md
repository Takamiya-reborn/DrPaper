# DrPaper

中文学术写作 Agent。基于 LLM tool-calling，多源检索真实文献（arXiv / OpenAlex / Semantic Scholar，按权威性分层调度）并生成结构规范、格式合规的中文论文初稿（Word 格式），供初学者模仿修改；同时提供论文诊断、润色、查新、实验设计与研究指导能力。

## 技术栈

- Python ≥ 3.13，依赖管理与打包使用 [uv](https://docs.astral.sh/uv/)
- [openai](https://pypi.org/project/openai/) SDK：调用 OpenAI 兼容接口的 LLM，流式输出 + tool-calling 循环
- [arxiv](https://pypi.org/project/arxiv/)：arXiv 文献检索（OpenAlex / Semantic Scholar 走 REST API，标准库实现）
- [python-docx](https://pypi.org/project/python-docx/)：生成符合学术排版规范的 docx

## 功能

**论文起草**

- **多源真实检索**：每条参考文献均来自检索 API 返回的元数据（标题/作者/编号）。arXiv 与 OpenAlex 作为 Tier-1 源先行检索，Semantic Scholar 作为 Tier-2 源仅在结果不足时调用（省配额）；结果跨源去重、按权威度（引用数/载体/年份）排序后截断返回，控制 token 消耗。正文引用与文献库一一对应，导出前自动校验，杜绝编造文献
- **生成初稿**：按"摘要 → 关键词 → 引言 → 相关工作 → 方法 → 实验设计 → 结论与展望"的标准结构生成，按用户要求控制字数
- **规范排版 docx**：A4 页面、黑体标题、宋体正文（西文 Times New Roman）、小四字号、1.5 倍行距、首行缩进 2 字符、两端对齐、悬挂缩进参考文献；排版规范由样式档案（YAML）驱动，可自定义（见[自定义排版样式](#自定义排版样式)）
- **预期数据估计**：写实验对比表前，Agent 先从文献库摘要中抽取真实实测数字（逐字校验，编造的数字无法通过），再做确定性统计估计——基线行是文献实测值（可溯源、标 [n] 引用），"本文方法"行是带†标记的预期区间：锚定最优基线、宽度取自文献间散布、下界刻意压低以对冲文献报告偏乐观的发表偏倚；可比实测值不足 3 条时拒绝估计、回退"【待补充】"占位。导出 Word 中†单元格黄色高亮并附批注，提醒替换为实测值（见[数据必须替换与勘误](#️-数据必须替换与勘误学术诚信底线)）
- **写作指导批注**：在各节标题处附带 Word 批注，讲解该节的写作要点

**论文修改**

- **论文诊断**：审查论文的结构、论证与表达，按严重程度分级定位问题，逐条给出修改建议
- **润色 / 查新 / 实验设计**：学术语言改写、基于 arXiv 的课题查新、实验方案设计，各有专精技能

**研究指导**

- **问方向**：问"某领域还有什么方向可做"，基于 arXiv 检索归纳 3~5 个可行方向，每个方向给出做什么、为什么现在可做（附真实文献证据）、最小可行课题与主要风险
- **推荐论文 + 接续工作**：推荐近 2~3 年可接续的论文，每篇说明核心贡献、作者自述的局限，以及你可以接着做的具体接续点（改什么、在什么数据上验证）
- **咨询不入库**：指导过程检索的文献只作参考，不进入论文引用文献库；决定动笔后再重新检索正式入库引用

**通用能力**

- **多轮修改与提问**：生成后可继续要求修改，或提问"摘要怎么写"之类的方法问题
- **按需加载 skills**：论文专精技能（起草、诊断、润色、实验设计、查新、研究指导）平时只在系统提示词中占一行索引，任务匹配时才由模型调用 `load_skill` 加载完整内容，显著降低 token 消耗

## 快速开始

需要 Python ≥ 3.13 与 [uv](https://docs.astral.sh/uv/)。

```bash
# 1. 安装依赖
uv sync

# 2. 配置 LLM API（OpenAI 兼容接口）
cp .env.example .env   # 然后填写 OPENAI_API_KEY，可选 OPENAI_BASE_URL 和 CURRENT_MODEL

# 3. 启动交互式会话
uv run drpaper
```

示例输入：

> 研究方向是大语言模型，课题是"检索增强生成缓解大模型幻觉"，正文 5000 字左右。

研究指导示例：

> 大语言模型在教育领域还有什么方向可做？推荐几篇能接着做的论文。

生成结果在 `.output/<论文题目>/` 目录：`<论文题目>.docx`（交付文档）、`draft.md`（正文源稿）、`references.json`（文献库）。

### 打包为 exe（可选）

需要分发给没有 Python 环境的 Windows 机器时，用 PyInstaller 打包：

```bash
uv run pyinstaller package.spec --noconfirm
```

产物为 `dist/drpaper/drpaper.exe`（单文件夹模式），整个 `dist/drpaper/` 文件夹需一起拷贝。exe 的使用方式与源码运行一致：

1. 在运行 exe 的目录下创建 `.env` 文件（格式同 `.env.example`），填写 OPENAI_API_KEY
2. 运行 `drpaper.exe`，进入交互式会话，输入方式同上
3. 生成结果输出到运行目录下的 `.output/<论文题目>/`

自定义 skills 与自定义排版样式、检索源配置一致：打包运行放 **exe 同级目录**（`skills/`、`profiles/`、`sources.yaml`），源码运行为 `~/.drpaper/` 下对应路径，均无需重新打包。

## 项目结构

按类别分层，依赖方向自上而下：

```
src/drpaper/
  __init__.py            # main() 入口
  __main__.py            # python -m drpaper
  runtime.py             # 用户资源目录定位（内置 + 用户两级资源的公共基建）
  app/                   # 【应用层】入口与配置
    cli.py               #   REPL 交互循环
    config.py            #   .env 配置加载
  agent/                 # 【Agent 层】编排核心
    core.py              #   会话状态 + tool-calling 主循环
    prompts.py           #   系统提示词
    tools.py             #   LLM 工具注册（检索/估计/导出）
  llm/                   # 【模型层】LLM 客户端封装
    client.py            #   流式对话 + 工具执行循环
  literature/            # 【文献层】可替换的检索数据源与聚合策略
    base.py              #   Paper 模型 + SearchProvider 协议（扩展点）+ 字段清洗原语
    sources/             #   各检索源实现
      arxiv.py           #     arXiv 实现
      openalex.py        #     OpenAlex 实现
      semantic_scholar.py#     Semantic Scholar 实现
      http.py            #     检索源共用 HTTP 客户端（限流退避重试）
    authority.py         #   权威度评分（引用数/载体/年份）
    aggregator.py        #   聚合器：分层检索 + 会话预算 + 跨源去重 + 排序
    sources.yaml         #   内置检索源配置
    sources_config.py    #   sources.yaml 两级加载（内置 + 用户覆盖）
    present.py           #   检索结果紧凑呈现（裁剪进对话历史）
  paper/                 # 【论文层】领域数据与规则
    draft.py             #   草稿 + 文献库 + 引用校验
    markdown_parser.py   #   Markdown 子集解析
    wordcount.py         #   中文字数统计
    metrics.py           #   指标库：摘要实测数字抽取 + 预期区间估计 + †单元格校验
  skills/                # 【技能层】论文专精 skills，按需加载
    manager.py           #   SkillManager：索引 + 渐进式加载
    builtin/             #   内置技能包（SKILL.md + references/）
  export/                # 【导出层】交付格式渲染
    style_profile.py     #   样式档案：内置+用户两级目录的排版规范加载
    profiles/            #   内置排版样式档案（YAML）
    guides.py            #   写作指导批注（按节标题关键词匹配）
    docx_writer.py       #   docx 导出（字体/缩进/批注）
```

## 扩展文献源

`literature/base.py` 定义了 `SearchProvider` 协议。新增数据源只需两步：

1. 在 `literature/sources/` 下新建实现类，返回 `Paper` 列表，并在 `literature/aggregator.py` 的
   `_PROVIDER_FACTORIES` 中登记源名与构造方式
2. 在 `sources.yaml` 的 `sources:` 下加一个条目（或复制到用户配置覆盖：打包运行放
   exe 同级 `sources.yaml`，源码运行为 `~/.drpaper/sources.yaml`，无需重新打包）

检索源配置项：`enabled`（开关）、`tier`（数字小者优先检索，高层结果足够则不调低层源）、
`weight`（权威分权重，影响合并排序）、`max_results`（单次向该源请求的条数上限）、
`per_session_calls`（每会话对该源的最大调用次数，即预算分配）。
API key 建议放 `.env`（`SEMANTIC_SCHOLAR_API_KEY` / `OPENALEX_MAILTO`），优先于 YAML。

## 自定义 skills

在用户技能目录下新建文件夹并放置 `SKILL.md` 即可，无需改动源码、
重新打包也不会丢失（与内置技能合并加载，同名时用户版覆盖内置版）：

| 运行方式 | 用户技能目录         |
| -------- | -------------------- |
| 打包 exe | exe 同级的 `skills/` |
| 源码运行 | `~/.drpaper/skills/` |

```
<用户技能目录>/
  my-skill/
    SKILL.md            # 必须，frontmatter 需含 name 与 description
    references/         # 可选，参考文件，agent 用 load_skill 的 file 参数按需读取
```

`SKILL.md` 格式（与内置技能一致）：

```markdown
---
name: my-skill
description: 一句话说明何时触发（agent 据此决定是否加载）
---

具体的方法论与操作步骤正文……
```

启动后 agent 的系统提示词索引会自动出现该技能，命中时模型通过 `load_skill` 加载完整内容。

## 自定义排版样式

排版规范（字体、字号、页边距、行距等）由样式档案（YAML）驱动，内置 + 用户两级目录，
与自定义 skills 同款模式——同名档案用户目录覆盖内置版：

| 运行方式 | 用户样式目录           |
| -------- | ---------------------- |
| 打包 exe | exe 同级的 `profiles/` |
| 源码运行 | `~/.drpaper/profiles/` |

放入名为 `default.yaml` 的档案即作为默认排版规范；也可以放多个档案（如 `hnu-thesis.yaml`、`ieee.yaml`）按名称选用。

档案格式（与内置 `profiles/thesis-generic.yaml` 一致，建议复制一份改值）：

```yaml
name: 某大学学位论文 # 档案显示名，缺省取文件名

fonts:
  cn_body: 宋体 # 中文正文字体
  cn_heading: 黑体 # 中文标题字体
  en: Times New Roman # 西文字体

sizes: # 字号：pt 为程序唯一真值，zh 仅作阅读对照
  title: { zh: 三号, pt: 16 }
  heading1: { zh: 四号, pt: 14 }
  body: { zh: 小四, pt: 12 }
  reference: { zh: 五号, pt: 10.5 }

page:
  margin_cm: 2.54 # 页边距

paragraph:
  line_spacing: 1.5 # 行距倍数
  first_line_indent_chars: 2 # 首行缩进（按正文字号计的字符数）
```

档案在启动时加载并校验，文件缺失或字段不合法会直接提示，不会等到导出才报错。

## ⚠️ 数据必须替换与勘误（学术诚信底线）

**表格中黄色高亮的†数值是写作参照，不是实验结果，论文里不允许留下任何一个。**

- †区间是基于文献统计的**预期参考值**，不是你的实验数据。直接把它当作实验结果留在论文中，属于编造数据，是学术造假。完成实验后必须逐个替换为自己的实测值，并同步改写对应分析段——"预期 / 有望"的口径要改成实际结论
- 预期区间的唯一用途是帮助你在动手实验前判断结果量级是否合理：如果实测远低于区间，先怀疑实验设置有误；如果确实达不到，如实报告并分析原因，这同样是正常且诚实的结果
- 基线行的文献实测值虽可溯源至原文献，也须核对你复现时的版本、数据划分与评测协议是否一致，不一致时勘误并说明
- "【待补充】"占位与预期区间一样，都必须在实验后补齐为真实数据
- 生成的文本仅供学习论文结构与写法，作为正式成果提交前请务必核实所有引用内容
