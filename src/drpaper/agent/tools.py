"""Agent 工具：把文献检索、分节起草与 docx/LaTeX 导出包装成 LLM 可调用的工具。

ToolBox 只做参数解析与结果装配，具体能力分层委托：
文献呈现 literature.present、字数与大纲校验 paper.draft、
历史折叠 agent.history、文件命名 export.naming、skill 去重 skills.manager、
公式转换 export.math、LaTeX 渲染 export.tex_writer；
流程阶段文档按需注入 workflows.manager；任务清单 agent.todo 随会话快照
（agent.session）持久化，每次工具调用后落盘，支撑断点续跑；
系统性调研、逐节写作与审校分别委托 agents/ 下的三个子代理，
主对话只回收结构化摘要，不持有子代理的中间过程。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from drpaper.agent.history import fold_old_section_drafts
from drpaper.agent.prompts import build_system_prompt
from drpaper.agent.schemas import (
    HINT_CITATION,
    HINT_EXPECTED_VALUE,
    HINT_INCOMPLETE,
    HINT_NOT_CONFIRMED,
    HINT_OUTLINE_PENDING,
    HINT_TOO_LONG,
    HINT_TOO_SHORT,
    HINT_TOTAL_SHORT,
    HINT_UNQUANTIFIED,
)
from drpaper.agent.session import SessionStore
from drpaper.agent.todo import TodoList
from drpaper.agents import ResearchAgent, ReviewerAgent, WriterAgent
from drpaper.eval import run_evaluation
from drpaper.eval.claims import scan_claims
from drpaper.eval.config import EvalConfig, load_baseline, load_eval_config
from drpaper.eval.tasks import HARD_KINDS
from drpaper.export.docx_writer import export_docx
from drpaper.export.naming import sanitize_filename
from drpaper.export.ppt_outline_writer import export_ppt_outline
from drpaper.export.style_profile import StyleProfile
from drpaper.export.tex_writer import export_tex
from drpaper.literature.base import SearchProvider
from drpaper.literature.present import paper_card
from drpaper.llm.client import ChatCallbacks, LLMClient
from drpaper.llm.types import ContextCompactor
from drpaper.paper.draft import Draft, Section, parse_outline
from drpaper.paper.markdown_parser import TableNode, extract_citations, parse
from drpaper.paper.metrics import MetricStore, estimate_graded, expected_cell_errors
from drpaper.paper.wordcount import count_chars
from drpaper.skills.manager import SkillManager
from drpaper.workflows import WorkflowManager


def _result(**fields: Any) -> str:
    """装配给模型看的 JSON 结果。"""
    return json.dumps(fields, ensure_ascii=False)


def _error(message: str, **fields: Any) -> str:
    return _result(error=message, **fields)


@dataclass
class ToolBox:
    """持有会话状态（草稿、检索源、输出目录、消息历史）的工具集合。"""

    provider: SearchProvider
    llm: LLMClient
    draft: Draft
    output_dir: str
    style: StyleProfile
    skills: SkillManager
    skill_index: str  # 系统提示词的 skill 索引，改目标字数后需重建提示词
    workflows: WorkflowManager  # 流程文档管理：进入新阶段时随工具结果注入阶段文档
    workflow_index: str  # 系统提示词的 workflow 索引，改目标字数后需重建提示词
    store: SessionStore | None = None  # 会话状态落盘：每次工具调用后写快照；None 不持久化
    messages: list[dict[str, Any]] = field(default_factory=list)
    todo: TodoList = field(default_factory=TodoList)  # 任务清单：随会话持久化，断点续跑依据
    resume_block: str = ""  # 断点恢复提示块，重建系统提示词（改字数）时需原样保留
    target_chars: int = 5000
    metrics: MetricStore = field(default_factory=MetricStore)
    eval_config: EvalConfig = field(default_factory=load_eval_config)
    baseline: dict[str, float] = field(default_factory=load_baseline)
    # 当前 chat 的界面回调，PaperAgent.chat 每轮注入；子代理透传工具事件
    callbacks: ChatCallbacks | None = None
    # 上下文压缩器：主循环与子代理共用（PaperAgent 注入）
    compactor: ContextCompactor | None = None
    research_notes: list[dict[str, Any]] = field(default_factory=list)  # 调研资料卡（编号+标题+要点）
    section_digests: dict[str, str] = field(default_factory=dict)  # 各节内容摘要（主循环持有的"各章摘要"）
    subagent_usage: dict[str, int] = field(
        default_factory=lambda: {"prompt": 0, "completion": 0}
    )  # 子代理 token 用量（与主循环分开统计）
    _issued_cells: list[str] = field(default_factory=list)
    _outline_confirmed: bool = False  # 提纲确认硬门禁：确认前禁止 submit_section / write_section
    # 提纲已提交且尚未等到用户表态：为真时 confirm_outline 拒绝自行确认。
    # 用标记而非历史索引——上下文压缩会改写历史，循环内注入的纠偏提示
    # 也是 user 角色，都不能算用户表态；只有真实用户输入算（note_user_turn）
    _awaiting_outline_reply: bool = False

    def handlers(self) -> dict[str, Any]:
        """工具名 → 处理函数，供 LLM 循环执行；统一包裹阶段文档注入。"""
        raw: dict[str, Any] = {
            "search_literature": self._search_literature,
            "set_target_chars": self._set_target_chars,
            "estimate_metric": self._estimate_metric,
            "research_topic": self._research_topic,
            "begin_draft": self._begin_draft,
            "confirm_outline": self._confirm_outline,
            "write_section": self._write_section,
            "submit_section": self._submit_section,
            "review_draft": self._review_draft,
            "finish_draft": self._finish_draft,
            "evaluate_draft": self._evaluate_draft,
            "remove_papers": self._remove_papers,
            "export_docx": self._export_docx,
            "load_skill": self._load_skill,
            "read_todo": self._read_todo,
            "update_todo": self._update_todo,
        }
        return {
            name: self._with_persist(self._with_stage_doc(name, fn))
            for name, fn in raw.items()
        }

    def _with_persist(self, fn: Any) -> Any:
        """调用成功后把会话快照落盘一次（断点续跑：状态变更即时持久化）。"""

        def wrapped(args: dict[str, Any]) -> str:
            result = fn(args)
            if self.store is not None:
                self.store.save(self.snapshot())
            return result

        return wrapped

    def _with_stage_doc(self, name: str, fn: Any) -> Any:
        """调用后把当前阶段的流程文档附在工具结果尾部（WorkflowManager 去重）。"""

        def wrapped(args: dict[str, Any]) -> str:
            result = fn(args)
            doc = self.workflows.enter_doc(name)
            return f"{result}\n\n---\n\n{doc}" if doc else result

        return wrapped

    # ---- 文献检索 ----

    def _search_literature(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return _error("query 不能为空")
        max_results = min(int(args.get("max_results", 5)), 10)
        register = args.get("register", True) is not False
        # 已入库的文献只回编号，避免重复检索把完整记录再灌一遍历史
        known = {p.uid for p in self.draft.references} if register else set()
        papers = self.provider.search(query, max_results)
        numbers = self.draft.add_papers(papers) if register else None
        results = []
        for paper in papers:
            if paper.uid in known:
                results.append(
                    {
                        "index": numbers[paper.uid],
                        "title": paper.title,
                        "in_library": True,
                    }
                )
                continue
            record = paper_card(paper, include_url=not register)
            if register:
                record["index"] = numbers[paper.uid]
            results.append(record)
        return _result(query=query, results=results)

    # ---- 目标字数 ----

    def _set_target_chars(self, args: dict[str, Any]) -> str:
        raw = args.get("target_chars")
        try:
            target = int(raw)
        except (TypeError, ValueError):
            return _error("target_chars 必须是整数")
        if target < 500:
            return _error(
                "目标字数过小（最低 500）。若用户描述的是局部篇幅，"
                "应改用 begin_draft 的各节预算表达，不要调用本工具"
            )
        if target == self.target_chars:
            return _result(
                status="ok", target_chars=target, note="与当前目标一致，未变更"
            )
        self.target_chars = target
        self.messages[0]["content"] = (
            build_system_prompt(self.skill_index, self.workflow_index, target)
            + self.resume_block
        )
        return _result(status="ok", target_chars=target)

    # ---- 指标估计 ----

    def _estimate_metric(self, args: dict[str, Any]) -> str:
        dataset = str(args.get("dataset", "")).strip()
        metric = str(args.get("metric", "")).strip()
        higher = args.get("higher_is_better", True) is not False
        if not dataset:
            return _error("dataset 不能为空")
        self.metrics.ensure_extracted(self.llm, self.draft.references)
        matched, available = self.metrics.match(dataset, metric)
        # 降级用：同指标跨数据集的记录（metric 为空时跨库匹配无意义，传空跳过该级）
        cross = self.metrics.match("", metric)[0] if metric else []
        if not matched and not cross:
            return _result(
                status="no_match",
                available=available,
                hint="文献库中没有该指标/数据集的任何可比实测值；该单元格用【待补充：……】占位，或先检索相关文献再重试",
            )
        result = estimate_graded(
            matched, cross, dataset, metric, higher_is_better=higher
        )
        if result["status"] != "ok":
            return _result(
                **result,
                hint=f"可比实测值不足（本数据集 {result['n']} 条，同指标跨数据集 {result['cross_n']} 条），"
                "降级估计仍无法给出可靠区间；该单元格用【待补充：……】占位，可先检索相关文献再重试",
            )
        index = {p.uid: i for i, p in enumerate(self.draft.references, start=1)}
        result["baselines"] = [
            {
                "ref": index[r.uid],
                "dataset": r.dataset,
                "method": r.method,
                "value": r.value,
            }
            for r in result.pop("sources")
        ]
        self._issued_cells.append(result["cell"])
        return _result(**result)

    # ---- 子代理派发（调研 / 写作 / 审校）----

    def _sub_callbacks(self) -> ChatCallbacks:
        """子代理的事件回调：透传工具与轮次事件，token 单独计入 subagent_usage。"""
        wrapped = ChatCallbacks()
        if self.callbacks is not None:
            wrapped.on_round_start = self.callbacks.on_round_start
            wrapped.on_tool_start = self.callbacks.on_tool_start
            wrapped.on_tool_end = self.callbacks.on_tool_end
        usage = self.subagent_usage

        def _on_usage(prompt: int, completion: int, _total: int) -> None:
            usage["prompt"] += prompt
            usage["completion"] += completion

        wrapped.on_usage = _on_usage
        return wrapped

    def _research_topic(self, args: dict[str, Any]) -> str:
        topic = str(args.get("topic", "")).strip()
        if not topic:
            return _error("topic 不能为空")
        agent = ResearchAgent(
            llm=self.llm, search=self._search_literature, compactor=self.compactor
        )
        outcome = agent.run(topic, callbacks=self._sub_callbacks())
        if outcome["status"] != "ok":
            return _error(outcome["reason"])
        # 资料卡按编号合并去重后存入会话状态，写作子代理经任务注入取用
        merged = {card["index"]: card for card in self.research_notes}
        merged.update({card["index"]: card for card in outcome["findings"]})
        self.research_notes = sorted(merged.values(), key=lambda card: card["index"])
        return _result(
            status="ok",
            summary=outcome["summary"],
            findings=self.research_notes,
            note="资料卡已存入会话状态，write_section 时自动提供给写作子代理",
        )

    def _write_section(self, args: dict[str, Any]) -> str:
        if not self._outline_confirmed:
            return _error("提纲未经用户确认，禁止写作", hint=HINT_NOT_CONFIRMED)
        name = str(args.get("name", "")).strip()
        guidance = str(args.get("guidance", "")).strip()
        try:
            section = self.draft.get_section(name)
        except KeyError as exc:
            return _error(str(exc), missing=self.draft.missing_sections())
        writer = WriterAgent(
            llm=self.llm,
            draft=self.draft,
            estimate_metric=self._estimate_metric,
            section_name=name,
            compactor=self.compactor,
        )
        outcome = writer.run(self._writer_task(section, guidance), callbacks=self._sub_callbacks())
        if outcome["status"] != "ok":
            return _error(
                f"写作子代理未能提交该节: {outcome['reason']}",
                hint="可携带更具体的 guidance 重试，或改用 submit_section 直接提交",
            )
        self.section_digests[name] = outcome["digest"]
        fold_old_section_drafts(self.messages, name)
        fields: dict[str, Any] = {
            "status": "ok",
            "section": name,
            "chars": outcome["chars"],
            "budget": outcome["budget"],
            "digest": outcome["digest"],
            "progress": self._progress(),
        }
        if outcome.get("warning"):
            fields["warning"] = outcome["warning"]
        return _result(**fields)

    def _writer_task(self, section: Section, guidance: str) -> str:
        """组装写作子代理的任务描述：大纲、前文摘要、资料卡与补充要求。"""
        outline = "\n".join(
            f"- {s.name}（预算 {s.budget or '不限'} 字{'，已完成' if s.content else ''}）"
            for s in self.draft.sections
        )
        done = "\n".join(
            f"- {name}：{digest}"
            for name, digest in self.section_digests.items()
            if name != section.name
        )
        notes = "\n".join(
            f"- [{card['index']}] {card['title']}——{card['key_point']}"
            for card in self.research_notes
        ) or "（暂无调研资料卡，可调用 read_reference 查看文献库现有文献）"
        current = (
            f"\n该节当前已有内容（本次为重写，按 guidance 调整后重新提交）：\n{section.content}\n"
            if section.content
            else ""
        )
        return (
            f"论文题目：{self.draft.title}\n"
            f"大纲与各节预算：\n{outline}\n"
            f"你负责撰写：「{section.name}」，字数预算 {section.budget or '不限'} 字。\n"
            f"{current}"
            + (f"已完成各节的内容摘要（承接衔接，禁止重复其内容）：\n{done}\n" if done else "")
            + f"调研资料卡（引用编号 [n] 以此为准）：\n{notes}\n"
            f"（文献库当前共 {len(self.draft.references)} 条，可用 read_reference 查看某条详情）\n"
            + (f"补充要求：{guidance}\n" if guidance else "")
            + "写完该节后调用 save_section 提交。"
        )

    def _review_draft(self, args: dict[str, Any]) -> str:
        written = [s for s in self.draft.sections if s.content]
        if not written:
            return _error("尚无已提交的章节内容，先 write_section 再审校")
        focus = str(args.get("focus", "")).strip()
        reviewer = ReviewerAgent(llm=self.llm, compactor=self.compactor)
        outcome = reviewer.run(
            self._reviewer_task(written, focus), callbacks=self._sub_callbacks()
        )
        if outcome["status"] != "ok":
            return _error(outcome["reason"])
        return _result(
            status="ok",
            summary=outcome["summary"],
            issues=outcome["issues"],
            note="意见需逐条处理：整节改动走 write_section 附 guidance，小改直接 submit_section",
        )

    def _reviewer_task(self, written: list[Section], focus: str) -> str:
        """组装审校子代理的任务描述：已提交章节全文 + 文献条目 + 关注点。"""
        body = "\n\n".join(f"## {s.name}\n\n{s.content}" for s in written)
        refs = "\n".join(self.draft.reference_lines())
        try:
            checklist = self.skills.load("paper-review")
        except KeyError:  # 未安装审校 skill 时跳过参考清单
            checklist = ""
        extra = f"\n\n审校参考清单（内置 paper-review skill）：\n{checklist}\n" if checklist else ""
        return (
            f"论文题目：{self.draft.title}\n\n"
            f"已提交章节全文：\n\n{body}\n\n"
            f"参考文献库：\n{refs}\n"
            f"{extra}"
            + (f"用户关注的审校重点：{focus}\n" if focus else "")
            + "审校完成后调用 submit_review 提交意见列表。"
        )

    def _begin_draft(self, args: dict[str, Any]) -> str:
        title = str(args.get("title", "")).strip()
        if not title:
            return _error("title 不能为空")
        try:
            sections = parse_outline(args.get("sections"))
        except ValueError as exc:
            return _error(str(exc))
        self.draft.begin(title, sections)
        # 重新提交提纲即重置确认状态：每次动笔前都要过一遍用户确认
        self._outline_confirmed = False
        self._awaiting_outline_reply = True
        fields: dict[str, Any] = {
            "status": "pending_confirmation",
            "sections": [{"name": s.name, "budget": s.budget} for s in sections],
            "target_chars": self.target_chars,
            "hint": HINT_OUTLINE_PENDING,
        }
        total_budget = sum(s.budget for s in sections)
        deviation = abs(total_budget - self.target_chars) / max(self.target_chars, 1)
        if deviation > 0.2:
            fields["warning"] = (
                f"各节预算之和 {total_budget} 与目标字数 {self.target_chars} 偏差较大，请调整大纲"
            )
        return _result(**fields)

    def _confirm_outline(self, _args: dict[str, Any]) -> str:
        """标记提纲已经用户确认；结构性校验：用户未回应提纲前不得自行确认。"""
        if not self.draft.sections:
            return _error("尚未提交提纲，应先调用 begin_draft")
        if self._outline_confirmed:
            return _result(status="ok", note="提纲此前已确认，可直接 submit_section")
        if self._awaiting_outline_reply:
            return _error(
                "用户尚未对提纲作出回应，禁止自行确认；"
                "请结束本轮，向用户展示提纲并等待回复"
            )
        self._outline_confirmed = True
        return _result(status="ok", note="提纲已确认，开始逐节 submit_section")

    def note_user_turn(self) -> None:
        """记录一次真实用户输入（PaperAgent.chat 开头调用）：解锁提纲确认。

        与消息历史无关——上下文压缩会改写历史，循环内注入的纠偏提示也是
        user 角色，都不能算用户表态；只有真实用户输入算。
        """
        self._awaiting_outline_reply = False

    def state_block(self) -> str:
        """压缩时随摘要一并注入的权威状态块：清单、提纲确认与文献编号对照。

        这些状态有 ToolBox 里的权威副本，不依赖模型转述，从机制上保证
        压缩后不遗忘已确认的提纲、字数要求与引用编号。
        """
        parts: list[str] = []
        if self.todo.steps:
            parts.append(f"任务清单（{self.todo.progress()}）：\n{self.todo.render()}")
        if self.draft.title:
            confirmed = "提纲已确认" if self._outline_confirmed else "提纲待用户确认"
            done = len(self.draft.sections) - len(self.draft.missing_sections())
            outline = "\n".join(
                f"- {s.name}（预算 {s.budget or '不限'} 字{'，已完成' if s.content else ''}）"
                for s in self.draft.sections
            )
            parts.append(
                f"当前草稿「{self.draft.title}」：目标 {self.target_chars} 字，"
                f"{confirmed}，已完成 {done}/{len(self.draft.sections)} 节。大纲：\n{outline}"
            )
        if self.draft.references:
            lines = "\n".join(
                f"[{i}] {p.title}"
                for i, p in enumerate(self.draft.references, start=1)
            )
            parts.append(f"文献库编号对照（引用编号以此为准）：\n{lines}")
        return "\n\n".join(parts)

    def _submit_section(self, args: dict[str, Any]) -> str:
        if not self._outline_confirmed:
            return _error("提纲未经用户确认，禁止提交正文", hint=HINT_NOT_CONFIRMED)
        name = str(args.get("name", "")).strip()
        content = str(args.get("content", "")).strip()
        if not content:
            return _error("content 不能为空")
        try:
            section = self.draft.get_section(name)
        except KeyError as exc:
            return _error(str(exc), missing=self.draft.missing_sections())
        if section.too_short(content):
            return _result(
                status="too_short",
                section=name,
                actual=count_chars(content),
                budget=section.budget,
                hint=HINT_TOO_SHORT,
            )
        if section.too_long(content):
            return _result(
                status="too_long",
                section=name,
                actual=count_chars(content),
                budget=section.budget,
                hint=HINT_TOO_LONG,
            )
        section.content = content
        fold_old_section_drafts(self.messages, name)
        fields: dict[str, Any] = {
            "status": "ok",
            "section": name,
            "chars": count_chars(content),
            "budget": section.budget,
            "progress": self._progress(),
        }
        total_refs = len(self.draft.references)
        over = sorted(n for n in extract_citations(content) if n < 1 or n > total_refs)
        if over:
            fields["warning"] = (
                f"引用编号超出文献库范围: {over}（当前共 {total_refs} 条）"
            )
        return _result(**fields)

    def _finish_draft(self, _args: dict[str, Any]) -> str:
        missing = self.draft.missing_sections()
        if missing:
            return _result(status="incomplete", missing=missing, hint=HINT_INCOMPLETE)
        # 防御性复核各节字数
        shorts = self.draft.short_sections()
        if shorts:
            section = shorts[0]
            return _result(
                status="too_short",
                section=section.name,
                actual=count_chars(section.content),
                budget=section.budget,
                hint=HINT_TOO_SHORT,
            )
        longs = self.draft.long_sections()
        if longs:
            section = longs[0]
            return _result(
                status="too_long",
                section=section.name,
                actual=count_chars(section.content),
                budget=section.budget,
                hint=HINT_TOO_LONG,
            )
        total = self.draft.total_chars()
        if self.draft.total_too_short(self.target_chars):
            return _result(
                status="too_short",
                total=total,
                target=self.target_chars,
                sections=self._sections_detail(),
                hint=HINT_TOTAL_SHORT,
            )
        self.draft.build_markdown()
        errors = self.draft.citation_errors()
        if errors:
            return _result(status="citation_error", errors=errors, hint=HINT_CITATION)
        errors = self._expected_value_errors()
        if errors:
            return _result(
                status="expected_value_error", errors=errors, hint=HINT_EXPECTED_VALUE
            )
        # 比较性断言量化门禁：强度/最高级断言未量化则阻塞，普通比较只警告
        unquantified = [
            f
            for f in scan_claims([(s.name, s.content) for s in self.draft.sections])
            if f.backed_by == "none"
        ]
        hard = [f for f in unquantified if f.kind in HARD_KINDS]
        if hard:
            return _result(
                status="unquantified_claims",
                errors=[asdict(f) for f in unquantified],
                hint=HINT_UNQUANTIFIED,
            )
        fields: dict[str, Any] = {
            "status": "ok",
            "total_chars": total,
            "sections": self._sections_detail(),
        }
        if unquantified:
            fields["warning"] = (
                "存在未量化的比较性表述（不阻塞导出，建议改写后重新提交对应节）"
            )
        return _result(**fields)

    def _evaluate_draft(self, _args: dict[str, Any]) -> str:
        """运行固定标准任务集，落盘 eval.json 并返回总分与逐任务 delta。"""
        self.draft.build_markdown()
        scorecard = run_evaluation(
            self.draft, self.llm, self._issued_cells, self.eval_config, self.baseline
        )
        out_dir = Path(self.output_dir) / sanitize_filename(
            self.draft.title or "未命名论文"
        )
        path = scorecard.save(out_dir / "eval.json")
        data = scorecard.to_dict()
        data["path"] = str(path)
        claims = next(t for t in scorecard.tasks if t.task == "claim_audit")
        if claims.detail["unquantified"]:
            data["hint"] = HINT_UNQUANTIFIED
        return _result(**data)

    def _expected_value_errors(self) -> list[str]:
        """校验全文表格中的†单元格都原样来自 estimate_metric 的返回。"""
        cells = [
            cell
            for node in parse(self.draft.markdown)
            if isinstance(node, TableNode)
            for row in node.rows
            for cell in row
        ]
        return expected_cell_errors(cells, self._issued_cells)

    def _remove_papers(self, args: dict[str, Any]) -> str:
        raw = args.get("indices", [])
        try:
            indices = [int(n) for n in raw]
        except (TypeError, ValueError):
            return _error("indices 必须是整数列表")
        # 编号会重排，按旧编号记录的资料卡随之失效
        self.research_notes = []
        return _result(result=self.draft.remove_papers(indices))

    # ---- 导出 ----

    def _export_docx(self, args: dict[str, Any]) -> str:
        errors = self.draft.citation_errors()
        if errors:
            return _result(status="blocked", errors=errors, hint=HINT_CITATION)
        include_guides = args.get("include_guides", True)
        if isinstance(include_guides, str):
            include_guides = include_guides.strip().lower() not in {"false", "0", "no"}
        name = sanitize_filename(self.draft.title or "未命名论文")
        out_dir = Path(self.output_dir) / name
        nodes = parse(self.draft.markdown)
        docx_path = out_dir / f"{name}.docx"
        export_docx(
            nodes,
            self.draft.references,
            docx_path,
            profile=self.style,
            include_guides=include_guides,
        )
        references_path = self.draft.save_references(out_dir)
        ppt_json, ppt_markdown = export_ppt_outline(self.draft, out_dir, self.llm)
        fields: dict[str, Any] = {
            "status": "ok",
            "docx": str(docx_path),
            "related_files": [str(references_path), str(ppt_json), str(ppt_markdown)],
            "ppt_outline": str(ppt_json),
            "ppt_summary": str(ppt_markdown),
            "references": len(self.draft.references),
        }
        try:
            tex_path = export_tex(nodes, self.draft.references, out_dir / f"{name}.tex")
            fields["tex"] = str(tex_path)
        except Exception as exc:  # tex 导出失败不阻塞 docx 交付
            fields["tex_warning"] = f"LaTeX 源码导出失败: {exc}"
        return _result(**fields)

    # ---- skill 加载 ----

    def _load_skill(self, args: dict[str, Any]) -> str:
        name = str(args.get("name", "")).strip()
        file = args.get("file")
        try:
            return self.skills.load_once(name, str(file) if file else None)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            return _error(str(exc))

    # ---- 任务清单 ----

    def _read_todo(self, _args: dict[str, Any]) -> str:
        if not self.todo.steps:
            return _result(steps=[], note="当前没有任务清单")
        return _result(steps=self.todo.to_json(), progress=self.todo.progress())

    def _update_todo(self, args: dict[str, Any]) -> str:
        try:
            self.todo.replace(args.get("steps"))
        except ValueError as exc:
            return _error(str(exc))
        return _result(
            status="ok",
            steps=self.todo.to_json(),
            progress=self.todo.progress(),
            note="清单已持久化到磁盘，进程中断后重启可据此续跑",
        )

    # ---- 内部装配 ----

    def snapshot(self) -> dict[str, Any]:
        """当前可恢复状态快照（不含消息历史），供 SessionStore 落盘。"""
        return {
            "todo": self.todo.to_json(),
            "target_chars": self.target_chars,
            "outline_confirmed": self._outline_confirmed,
            "draft": self.draft.dump_state(),
            "research_notes": self.research_notes,
            "section_digests": self.section_digests,
            "issued_cells": self._issued_cells,
        }

    def restore(self, state: dict[str, Any]) -> None:
        """从上次会话快照恢复状态；坏块跳过，尽可能多恢复。"""
        self.todo.load_json(state.get("todo"))
        try:
            self.target_chars = int(state.get("target_chars", self.target_chars))
        except (TypeError, ValueError):
            pass
        self._outline_confirmed = state.get("outline_confirmed") is True
        # 恢复后消息历史从零开始，「用户是否已回应提纲」以恢复后的首条用户输入为准
        self._awaiting_outline_reply = False
        draft_state = state.get("draft")
        if isinstance(draft_state, dict):
            self.draft.load_state(draft_state)
        if isinstance(state.get("research_notes"), list):
            self.research_notes = state["research_notes"]
        if isinstance(state.get("section_digests"), dict):
            self.section_digests = state["section_digests"]
        if isinstance(state.get("issued_cells"), list):
            self._issued_cells = state["issued_cells"]

    def _progress(self) -> str:
        missing = self.draft.missing_sections()
        done = len(self.draft.sections) - len(missing)
        progress = f"已完成 {done}/{len(self.draft.sections)} 节"
        if not missing:
            return progress
        # 写下一节前把预算送到眼前，比在提示词里重复字数更省也更醒目
        upcoming = self.draft.get_section(missing[0])
        budget = f"预算 {upcoming.budget} 字" if upcoming.budget else "不限字数"
        return f"{progress}；下一节「{upcoming.name}」{budget}"

    def _sections_detail(self) -> list[dict[str, Any]]:
        return [
            {"name": s.name, "chars": count_chars(s.content), "budget": s.budget}
            for s in self.draft.sections
        ]
