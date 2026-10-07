"""公式转换：把 LaTeX 公式转为 Word 原生 OMML 公式元素。

转换链路 latex2mathml（LaTeX→MathML）→ mathml2omml（MathML→OMML）；
失败时返回 None，由调用方降级为斜体原文。
"""

from __future__ import annotations

import latex2mathml.converter
import mathml2omml
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls


def latex_to_omml(latex: str):
    """LaTeX 公式源码 → oMath XML 元素；转换失败返回 None。"""
    try:
        omml = mathml2omml.convert(latex2mathml.converter.convert(latex))
    except Exception:
        return None
    # mathml2omml 输出的根元素不带命名空间声明，parse_xml 需要补上
    if not omml.startswith("<m:oMath>"):
        return None
    return parse_xml(omml.replace("<m:oMath>", f"<m:oMath {nsdecls('m')}>", 1))
