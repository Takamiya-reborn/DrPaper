# DrPaper 用户手册

本手册面向拿到打包版 DrPaper、但没有 Python 和 uv 环境的 Windows 用户。发行版不需要安装 Python，也不需要执行 `uv sync`；只要准备好一个 OpenAI 兼容接口即可运行。

## 1. 解压并确认文件完整

发行版是**单文件夹模式**，不能只拷贝一个 exe。将发布者提供的压缩包完整解压到一个有写入权限的目录，例如：

```text
D:\Apps\DrPaper\
  drpaper.exe
  _internal\              # PyInstaller 运行时文件，不能删除
  ...                      # 其他随包文件
```

建议不要把程序放在 `C:\Program Files\` 等需要管理员权限的目录中，因为程序默认会在运行目录创建配置文件、用户资源目录和输出文件。解压后不要移动或删除 `drpaper.exe` 旁边的 `_internal` 及其他文件夹。

## 2. 创建 `.env` 并填写模型配置

在 **`drpaper.exe` 同级目录**新建一个名为 `.env` 的纯文本文件。注意文件名不是 `.env.txt`；在资源管理器中可以开启“文件扩展名”显示来确认。

最小配置如下：

```dotenv
OPENAI_API_KEY=你的_API_Key
```

如果使用 OpenAI 官方接口，通常只需要这一项。如果使用其他兼容 OpenAI API 的服务，再填写接口地址和模型名：

```dotenv
OPENAI_API_KEY=你的_API_Key
OPENAI_BASE_URL=https://你的服务地址/v1
CURRENT_MODEL=你的模型名称
```

可用配置项：

| 配置项                     | 必填 | 作用                                                               |
| -------------------------- | ---- | ------------------------------------------------------------------ |
| `OPENAI_API_KEY`           | 是   | LLM 服务的 API 密钥。DrPaper 不提供密钥，需向所用服务商申请        |
| `OPENAI_BASE_URL`          | 否   | OpenAI 兼容接口的根地址；使用官方 OpenAI 时通常留空                |
| `CURRENT_MODEL`            | 否   | 使用的模型名；不填时默认为 `gpt-4o-mini`，模型必须由接口服务商支持 |
| `SEMANTIC_SCHOLAR_API_KEY` | 否   | Semantic Scholar 密钥；用于提高文献检索速率，不填也可使用免费额度  |
| `OPENALEX_MAILTO`          | 否   | OpenAlex 礼貌池邮箱；用于提高请求稳定性，不填也可检索              |
| `DRPAPER_OUTPUT_DIR`       | 否   | 输出目录；默认为运行目录下的 `.output`                             |

示例：

```dotenv
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
CURRENT_MODEL=gpt-4o-mini
# SEMANTIC_SCHOLAR_API_KEY=可选
# OPENALEX_MAILTO=你的邮箱
# DRPAPER_OUTPUT_DIR=.output
```

请不要把包含真实 API Key 的 `.env` 发给他人、提交到 Git，或截图公开。更换服务商时只需修改 `.env`，不需要重新解压或重新打包。

## 3. 启动程序

推荐在 PowerShell 中启动，这样可以看到错误信息，也能方便传入本地文献库：

```powershell
cd D:\Apps\DrPaper
.\drpaper.exe
```

看到欢迎信息和 `你>` 提示后即可输入需求，例如：

```text
研究方向是大语言模型，课题是“检索增强生成缓解大模型幻觉”，正文 5000 字左右。
```

程序会先检索文献，再流式输出回答。生成论文时需要等待多个网络请求和模型调用完成，请不要在没有看到本轮结束前关闭窗口。输入 `exit` 或 `quit`，或者按 `Ctrl+C`，可以退出会话；单轮处理中按 `Ctrl+C` 会中断当前轮次，但通常可以继续输入下一条请求。

也可以在资源管理器中双击 `drpaper.exe` 启动。若窗口一闪而过，请改用上面的 PowerShell 方式启动，以便读取具体报错。

## 4. 查看生成结果

默认结果位于 exe 同级目录：

```text
D:\Apps\DrPaper\.output\<论文题目>\
  <论文题目>.docx       # 主要交付文档，可用 Word 打开
  <论文题目>.tex        # LaTeX 源码，公式保留，可自行编译
  ppt_outline.json       # PPT Agent 可读取的结构化大纲
  ppt_outline.md         # 可人工查看和修改的 PPT 大纲
  references.json        # 本次论文实际登记的文献库
  eval.json              # 质量评分卡（起草流程完成后生成）
```

如果设置了 `DRPAPER_OUTPUT_DIR`，上述 `.output` 会替换为该配置指定的目录。输出目录可以复制、备份或删除，不影响程序本身。论文题目来自对话内容，目录名经过文件名清理；不要在程序运行过程中手动修改当前论文的输出目录。

## 5. 使用自己的文献库

启动时用 `--library` 传入 BibTeX、RIS、JSONL 文件或目录，可以同时传入多个路径：

```powershell
.\drpaper.exe --library "D:\文献\RAG 课题" --library "D:\文献\重点论文.bib"
```

目录会递归读取支持的文件。用户文献会优先检索和引用；与在线结果重复时，优先保留用户文件中的元数据。支持格式和 JSONL 字段见 [README.md](README.md#外接本地文献库)。路径包含空格时必须用引号包起来。

如果希望每次启动都自动挂载同一批文献，可在 exe 同级创建 `sources.yaml`，在 `sources.local_library.paths` 中填写路径。路径可以写绝对路径，也可以写相对于 exe 运行目录的路径：

```yaml
sources:
  local_library:
    enabled: true
    paths:
      - "D:\\文献\\RAG 课题"
      - "my-refs.bib"
```

## 6. 自定义排版、技能和检索设置

发行版支持通过 exe 同级文件覆盖默认资源，不需要改源码或重新打包：

| 目录或文件      | 用途                                                        |
| --------------- | ----------------------------------------------------------- |
| `profiles\`     | 自定义 Word 排版样式；放入 `default.yaml` 可作为默认样式    |
| `skills\`       | 自定义 Agent 技能；每个技能目录必须包含 `SKILL.md`          |
| `sources.yaml`  | 开关和调整 arXiv、OpenAlex、Semantic Scholar 以及本地文献库 |
| `eval.yaml`     | 调整论文质量评估的权重和阈值                                |
| `baseline.yaml` | 保存自己的质量评分基线                                      |

第一次使用建议先不要修改这些文件，确认基本流程可运行后，再按 README 中的[自定义排版样式](README.md#自定义排版样式)和[自定义 skills](README.md#自定义-skills)操作。升级发行版时，建议先备份 exe 同级的 `.env`、`profiles\`、`skills\`、`sources.yaml` 以及 `.output\`，再替换程序文件。

## 常见问题

**提示“未检测到 `OPENAI_API_KEY`”**

确认 `.env` 与 `drpaper.exe` 在同一目录，文件名不是 `.env.txt`，并且配置格式为 `OPENAI_API_KEY=...`。从 PowerShell 进入 exe 目录后再运行，不要从不确定的工作目录启动。

**提示连接失败、超时或文献为空**

确认电脑可以访问 LLM 接口、arXiv、OpenAlex 和 Semantic Scholar；公司代理或防火墙可能需要放行这些请求。若 LLM 服务商不是 OpenAI 官方，请检查 `OPENAI_BASE_URL` 是否为该服务商要求的 OpenAI 兼容地址，并确认 `CURRENT_MODEL` 名称正确。

**双击后窗口立即关闭**

在 PowerShell 中运行 `.\drpaper.exe`，保留错误输出再排查。常见原因是 `.env` 缺失、API Key 无效、模型名错误或网络不可用。

**中文或状态动画显示异常**

使用 Windows Terminal 或 PowerShell 启动，并确保终端使用 UTF-8。程序会尽量降级处理不支持的字符，但旧版控制台可能无法正确显示旋转符号。

**Word 中的公式或字体与预期不同**

Word 原生公式转换依赖本机 Word 的显示能力；字体需要在本机安装。转换失败时文档会保留可读的公式文本，同时可以使用同目录的 `.tex` 源码进行 LaTeX 编译。

## 发布者如何生成发行版

下面步骤只适用于项目维护者，不是普通用户的安装步骤。需要 Python ≥ 3.13 和 uv：

```bash
uv sync
uv run pyinstaller package.spec --noconfirm
```

生成目录为 `dist/drpaper/`，其中包含 `drpaper.exe`。发布时必须将整个目录压缩后再分发；不要只发送 exe。可以先在一台没有 Python 环境的 Windows 机器上按本手册前面的步骤做一次干净测试，确认 `.env`、启动、联网检索和 Word 导出均正常。
