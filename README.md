# DrPaper

中文学术写作 Agent。基于 LLM tool-calling，在 arXiv 检索真实文献并生成结构规范、格式合规的中文论文初稿（Word 格式），供初学者模仿修改；同时提供论文诊断、润色、查新、实验设计与研究指导能力。

## 技术栈

- Python ≥ 3.13，依赖管理与打包使用 [uv](https://docs.astral.sh/uv/)
- [openai](https://pypi.org/project/openai/) SDK：调用 OpenAI 兼容接口的 LLM，流式输出 + tool-calling 循环
- [arxiv](https://pypi.org/project/arxiv/)：arXiv 文献检索
- [python-docx](https://pypi.org/project/python-docx/)：生成符合学术排版规范的 docx

## 功能

**论文起草**

- **arXiv 真实检索**：每条参考文献均来自 arXiv API 返回的元数据（标题/作者/编号），正文引用与文献库一一对应，导出前自动校验，杜绝编造文献
- **生成初稿**：按"摘要 → 关键词 → 引言 → 相关工作 → 方法 → 实验设计 → 结论与展望"的标准结构生成，按用户要求控制字数
- **规范排版 docx**：A4 页面、黑体标题、宋体正文（西文 Times New Roman）、小四字号、1.5 倍行距、首行缩进 2 字符、两端对齐、悬挂缩进参考文献；排版规范由样式档案（YAML）驱动，可自定义（见[自定义排版样式](#自定义排版样式)）
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

自定义 skills 照常放在 `~/.drpaper/skills/`；自定义排版样式则放在 **exe 同级的 `profiles/` 目录**（源码运行为 `~/.drpaper/profiles/`），均无需重新打包。

## 项目结构

按类别分层，依赖方向自上而下：

```
src/drpaper/
  __init__.py            # main() 入口
  __main__.py            # python -m drpaper
  app/                   # 【应用层】入口与配置
    cli.py               #   REPL 交互循环
    config.py            #   .env 配置加载
  agent/                 # 【Agent 层】编排核心
    core.py              #   会话状态 + tool-calling 主循环
    prompts.py           #   系统提示词
    tools.py             #   LLM 工具注册（检索/导出）
  llm/                   # 【模型层】LLM 客户端封装
    client.py            #   流式对话 + 工具执行循环
  literature/            # 【文献层】可替换的检索数据源
    base.py              #   Paper 模型 + SearchProvider 协议（扩展点）
    arxiv.py             #   arXiv 实现
  paper/                 # 【论文层】领域数据与规则
    draft.py             #   草稿 + 文献库 + 引用校验
    markdown_parser.py   #   Markdown 子集解析
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

`literature/base.py` 定义了 `SearchProvider` 协议。新增数据源（如 Semantic Scholar）只需：

1. 在 `literature/` 下新建实现类，返回 `Paper` 列表
2. 在 `agent/core.py` 中注册即可

## 自定义 skills

在用户技能目录 `~/.drpaper/skills/` 下新建文件夹并放置 `SKILL.md` 即可，无需改动源码、
重新打包也不会丢失（与内置技能合并加载，同名时用户版覆盖内置版）：

```
~/.drpaper/skills/
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

## 注意事项

- 初稿中的实验数据用"【待补充】"占位，需自行替换为真实数据
- 生成的文本仅供学习论文结构与写法，作为正式成果提交前请务必核实所有引用内容
