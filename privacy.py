"""Fail-closed checks before a Codex answer leaves the local machine."""

from __future__ import annotations

import json
import ipaddress
import re
from urllib.parse import urlsplit


OUT_OF_SCOPE = "福头目前只回答产品功能、使用、需求和故障相关的问题。请换一个产品问题再问我。"
PERSONAL_REFUSAL = "福头不能查询或回答个人信息、个人近况、私人项目及私人会话的内容。请改问具体的产品问题。"
SENSITIVE_REFUSAL = "福头不能提供凭证、密钥、敏感配置或原始业务数据。请改问不涉及这些内容的产品问题。"
OPERATION_REFUSAL = "福头只提供产品问题的只读分析，不能替你运行服务、代码、脚本或测试，也不能修改项目。请改问现状、原因或实施建议。"

# Reject these requests before they reach a Codex chat or any local tool.
PERSONAL_QUESTION_PATTERNS = (
    re.compile(r"(?:私人|私密|个人|私有)(?:的)?(?:项目|仓库|文件|资料|会话|聊天|记录)"),
    re.compile(r"(?:另一个|另外|其他|别的).{0,10}(?:项目|仓库|代码库)"),
    re.compile(r"(?:我的|你的|他的|她的|家人|同事|员工|客户).{0,12}(?:生日|住址|家庭|婚姻|收入|工资|薪资|资产|病历|病史|健康|身份证|手机号|银行卡|私人邮箱)"),
    re.compile(r"[\u4e00-\u9fff]{2,4}的(?:生日|住址|家庭|婚姻|收入|工资|薪资|资产|病历|病史|健康|身份证|手机号|银行卡|私人邮箱)"),
    re.compile(r"(?:查|找|列出|显示|透露|告诉|读取|导出|总结|搜索|提供).{0,30}(?:个人信息|隐私信息|身份证号|手机号|银行卡号|家庭住址|病历|私人邮箱)"),
)

PERSONAL_ACTIVITY_PATTERN = re.compile(
    r"(?P<subject>[\u4e00-\u9fff]{1,12})(?:最近|近况|这段时间|现在|今天|昨天|本周|这周)"
    r".{0,12}(?:在干什么|干嘛|干啥|做什么|做了什么|忙什么|忙啥|做啥|怎么样|如何|在哪|在哪里|去了哪|去哪里|行踪|动态|负责什么|参与什么)")
PERSONAL_ACTIVITY_DIRECT_PATTERN = re.compile(
    r"(?P<subject>[\u4e00-\u9fff]{1,12})(?:在干什么|在干嘛|在干啥|在忙什么|在做什么|在哪里|在哪儿|去了哪|去哪里)")
NON_PERSON_SUBJECTS = ("项目", "产品", "系统", "服务", "应用", "页面", "接口", "数据", "报告", "指标", "审批", "审批流", "机器人", "任务", "流程", "平台", "功能", "模块", "代码", "仓库", "测试", "环境", "数据库", "前端", "后端", "公司", "部门", "团队")

SENSITIVE_REQUEST_PATTERNS = (
    re.compile(r"(?:密码|口令|密钥|凭证|令牌|token|secret|api.?key|连接串|环境变量)(?:是(?:什么|多少)|具体值|原文|内容|清单|列表|发给我|给我|告诉我|贴出来|展示出来)", re.I),
    re.compile(r"(?:把|将).{0,25}(?:密码|口令|密钥|凭证|令牌|token|secret|api.?key|连接串|环境变量).{0,20}(?:发给|告诉|贴出|输出|展示|导出)", re.I),
    re.compile(r"(?:查询|读取|找出|列出|导出|给我|发我|展示).{0,20}(?:完整|全部|所有|原始|真实|具体).{0,15}(?:客户数据|业务数据|数据库记录|生产数据|线上数据|配置值|环境变量|凭证|密钥|密码|token|api.?key)", re.I),
)

OPERATION_REQUEST_PATTERNS = (
    re.compile(r"(?:^|[\s，。；;])(?:UPDATE|DELETE|INSERT|ALTER|DROP|TRUNCATE|CREATE|MERGE|GRANT|REVOKE)\s+(?:\w|`)", re.I),
    re.compile(r"(?:执行|运行|跑|提交|帮我做|请做).{0,20}(?:UPDATE|DELETE|INSERT|ALTER|DROP|TRUNCATE|CREATE|MERGE|GRANT|REVOKE)\b", re.I),
    re.compile(r"^(?:通过|使用|用|借助).{0,30}(?:发布|部署|重启|停止|启动|修改|删除|更新).{0,12}(?:服务|应用|Pod|容器|数据|文件|配置|代码|项目)", re.I),
    re.compile(r"(?:请(?:你)?|帮我|麻烦|替我|给我|你来|你去|直接|现在就|顺便|然后|再|能不能|可不可以|是否可以)(?:在|对|把|将)?[\u4e00-\u9fffA-Za-z0-9_. /-]{0,25}(?:跑(?:一下)?|运行|执行|启动|重启|停止|部署|发布|安装|升级|修改|改动|改写|改(?:一下|下)?|修复|编辑|删除|创建|提交|推送|新增|添加|写)[\u4e00-\u9fffA-Za-z0-9_. /-]{0,40}(?:服务|脚本|代码|测试|单测|用例|回归|构建|命令|文件|配置|仓库|项目|依赖|容器|程序|bug|问题|字段|功能|\.sh|\.py|\.md)"),
    re.compile(r"^(?:请(?:你)?|帮我|麻烦(?:你)?|替我|给我|你来|你去|直接|现在就|能不能|可不可以|是否可以)?\s*(?:跑|运行|执行|启动|重启|停止|部署|发布|安装|升级|修改|改动|改写|改|修复|编辑|删除|创建|提交|推送|测试|构建|编译|新增|添加|写)(?:一下|下|个|掉|起来)?(?:\s|[，。！？?！]|$|.{0,20}(?:服务|脚本|代码|测试|单测|用例|回归|构建|命令|文件|配置|仓库|项目|依赖|容器|程序|bug|字段|功能|\.sh|\.py|\.md))"),
    re.compile(r"^(?:请(?:你)?|帮我|麻烦(?:你)?|替我|给我|直接|现在就).{0,20}(?:部署|发布|重启|启动|停止|构建|编译|提交|推送)(?:一下|下|好|起来)?\s*$"),
    re.compile(r"^(?:请(?:你)?|帮我|麻烦(?:你)?)?\s*(?:把|将)这个(?:改掉|改了|删掉|删除|修改)\s*$"),
    re.compile(r"(?:把|将)[\u4e00-\u9fffA-Za-z0-9_. /-]{0,20}(?:服务|脚本|代码|测试|命令|文件|配置|仓库|项目|依赖|容器|程序)[\u4e00-\u9fffA-Za-z0-9_. /-]{0,10}(?:跑|运行|执行|启动|重启|停止|部署|发布|安装|升级|修改|改动|改写|改|修复|编辑|删除|创建|提交|推送)"),
    re.compile(r"(?:run|execute|start|restart|stop|deploy|publish|install|modify|edit|delete|commit|push)\s+(?:the\s+)?(?:service|script|code|test|command|file|config|project|repository)", re.I),
    re.compile(r"(?:请|帮我|麻烦|直接|现在就).{0,12}(?:运行|执行|跑).{0,8}(?:python\d*|pytest|node|npm|pnpm|yarn|bash|sh\b|go\s+test|make\b|mvn|gradle|uv\s+run|poe\b)", re.I),
)

SENSITIVE_PROJECT_PATTERNS = (
    re.compile(r"(?:密钥|凭证|口令|令牌|密码|token|secret|api.?key)", re.I),
    re.compile(r"(?:内部|生产|线上|数据库|配置中心|环境变量|源码|源代码|日志|客户数据|真实数据|未公开).{0,8}(?:地址|账号|口令|密钥|连接串|接口路径|配置值|表名|字段名|原始数据|数据样本|实现细节)", re.I),
    re.compile(r"(?:IP\s*地址|内网|私网|连接串|表结构|具体实现|故障根因|权限策略|安全策略)", re.I),
)

NEGATED_SENSITIVE = re.compile(r"(?:不要|避免|无需|不需要|请勿|禁止|不涉及|不包含|不提供|无法提供|省略|隐去|不展示|不引用|不披露|不输出|不得)")

PERSONAL_OUTPUT_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"\b(?:ou_|oc_|om_)[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?:生日|家庭住址|身份证号|银行卡号|病历|薪资)\s*[:：]"),
)

SOURCE_CODE_PATTERNS = (
    re.compile(r"(?:^|[\s`])(?:/Users/[^\s`]+|src/[^\s`]+|[\w./-]+\.py(?::\d+)?)"),
    re.compile(r"`[^`\n]*(?:\([^`\n]*\)|\{[^`\n]*\}|;)[^`\n]*`"),
)

# These are never suitable for an IM answer, even after a review click.
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.I),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret[_-]?key)\s*[:=]\s*[^\s,;]{6,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)\b(?:jdbc|mysql|postgres(?:ql)?|mongodb|redis)://[^\s]+"),
)

HTTPS_LINK_PATTERN = re.compile(r"https://[^\s<>\])）。，,]+", re.I)


def is_shareable_page_link(url: str) -> bool:
    """Only exempt ordinary public page URLs from source-overlap checks."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or port
            or parsed.query or parsed.fragment or "." not in host
            or host.endswith((".local", ".lan", ".internal", ".test", ".invalid", ".example"))
            or any(part in {"internal", "intranet", "corp", "private"} for part in host.split("."))
            or re.search(r"/(?:token|secret|credential|api[_-]?key|auth)(?:/|=|$)", parsed.path, re.I)):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return False


def is_personal_question(question: str) -> bool:
    if any(pattern.search(question) for pattern in PERSONAL_QUESTION_PATTERNS):
        return True
    activity = PERSONAL_ACTIVITY_PATTERN.search(question) or PERSONAL_ACTIVITY_DIRECT_PATTERN.search(question)
    if not activity:
        return False
    subject = re.sub(r"^(?:请问|请分析|请查|帮我看看|帮我查|你知道|我想知道|问一下|关于|查查|看下|看看)+", "",
                     activity.group("subject"))
    return bool(subject) and not subject.endswith(NON_PERSON_SUBJECTS)


def is_sensitive_request(question: str) -> bool:
    return any(pattern.search(question) for pattern in SENSITIVE_REQUEST_PATTERNS)


def refusal_for_question(question: str) -> str | None:
    if is_personal_question(question):
        return PERSONAL_REFUSAL
    if is_sensitive_request(question):
        return SENSITIVE_REFUSAL
    if is_prohibited_operation_request(question):
        return OPERATION_REFUSAL
    return None


def is_prohibited_operation_request(question: str) -> bool:
    conceptual_operation = re.search(
        r"^(?:运行|执行|启动|重启|部署|发布|修改|改动|修复|测试).{0,15}(?:会有什么|会不会|有什么风险|为什么|如何|怎么|需要什么|有什么影响)",
        question)
    analysis_only = re.search(
        r"^(?:请(?:你)?|帮我|麻烦)?(?:分析|评估|说明|解释|介绍|梳理|讨论|讲讲|说说|判断|看看|看下).{0,24}(?:如何|怎么|可行性|难度|方案|风险|思路|需要|能否)",
        question)
    followup_action = re.search(
        r"(?:并|然后|顺便|接着|再).{0,12}(?:跑|运行|执行|启动|重启|停止|部署|发布|安装|升级|修改|改写|改一下|修复|编辑|删除|创建|提交|推送)",
        question)
    if (analysis_only or conceptual_operation) and not followup_action:
        return False
    return any(pattern.search(question) for pattern in OPERATION_REQUEST_PATTERNS)


def normalize_answer(answer: str) -> str:
    """Keep a single writing artifact's content, without Codex-only fence syntax."""
    match = re.search(r"(?ms)^:::writing\{[^\n]*\}\n(?P<body>.*?)\n:::\s*$", answer)
    return match.group("body").strip() if match else answer.strip()


def unsupported_tool_used(items: list[dict], *, allow_history: bool,
                          attachment_handles: set[str] | None = None) -> bool:
    """Reject file changes and unscoped attachment/history calls; other skills follow AGENTS.md."""
    for item in items:
        kind = item.get("type")
        if kind == "mcpToolCall":
            if (item.get("server") == "codex_app"
                    and item.get("tool") == "send_message_to_thread"):
                return True
            history_ok = (allow_history and item.get("server") == "codex_app"
                          and item.get("tool") in ("list_threads", "read_thread"))
            arguments = item.get("arguments") or {}
            attachment_ok = (item.get("server") == "futou_attachment"
                             and item.get("tool") == "fetch_feishu_attachment"
                             and isinstance(arguments, dict)
                             and arguments.get("handle") in (attachment_handles or set()))
            scoped_tool = (item.get("server") == "futou_attachment"
                           and item.get("tool") == "fetch_feishu_attachment")
            history_tool = (item.get("server") == "codex_app"
                            and item.get("tool") in ("list_threads", "read_thread"))
            if (scoped_tool and not attachment_ok) or (history_tool and not history_ok):
                return True
        elif kind in ("fileChange", "dynamicToolCall"):
            return True
    return False


def mentions_sensitive_project_detail(text: str) -> bool:
    for clause in re.split(r"[，,。；;]|但是|不过|并且|并|但", text):
        if NEGATED_SENSITIVE.search(clause):
            continue
        if any(pattern.search(clause) for pattern in SENSITIVE_PROJECT_PATTERNS):
            return True
    return False


def labeled_json_examples_only(answer: str) -> bool:
    """Accept illustrative JSON blocks while still rejecting executable code fences."""
    fences = list(re.finditer(r"(?m)^```([^\n]*)\n(.*?)^```[ \t]*$", answer, re.S))
    if not fences or len(fences) * 2 != answer.count("```"):
        return False
    for match in fences:
        if match.group(1).strip().lower() != "json":
            return False
        if len(match.group(2)) > 3000 or not re.search(
                r"虚构|示意|示例|演示", answer[max(0, match.start() - 600):match.start()]):
            return False
        try:
            example = json.loads(match.group(2))
        except ValueError:
            return False
        def safe_values(value: object) -> bool:
            if isinstance(value, str):
                return (len(value) <= 300 and "\n" not in value and
                        not re.search(r"(?i)\b(?:def|class|import|function|SELECT|INSERT|UPDATE|curl)\s+", value))
            if isinstance(value, list):
                return all(safe_values(item) for item in value)
            if isinstance(value, dict):
                return all(safe_values(key) and safe_values(item)
                           for key, item in value.items())
            return value is None or isinstance(value, (bool, int, float))
        if not safe_values(example):
            return False
    return True


def export_decision(answer: str, *, question: str = "", unsupported_tool: bool = False) -> str:
    """Return block, review, or auto based on content and tool provenance."""
    if not answer or len(answer) > 9000:
        return "block"
    if refusal_for_question(question) or unsupported_tool:
        return "block"
    if any(pattern.search(answer) for pattern in SECRET_PATTERNS):
        return "block"
    if any(pattern.search(answer) for pattern in PERSONAL_OUTPUT_PATTERNS):
        return "block"
    if any(pattern.search(answer) for pattern in SOURCE_CODE_PATTERNS):
        return "block"
    if ("```" in answer and not labeled_json_examples_only(answer)) or re.search(
            r"(?m)^\s*(?:curl\s|SELECT\s|INSERT\s|UPDATE\s|def\s|class\s|import\s|from\s+\w+\s+import\s)", answer, re.I):
        return "block"
    if answer.strip() == OUT_OF_SCOPE:
        return "review"
    if mentions_sensitive_project_detail(question) or mentions_sensitive_project_detail(answer):
        return "review"
    return "auto"


def has_long_verbatim_overlap(answer: str, tool_items: list[dict], window: int = 40) -> bool:
    """Block copied passages and shorter attributed quotes from local sources."""
    normalized_answer = " ".join(answer.split())
    shareable_spans = [(match.start(), match.end()) for match in HTTPS_LINK_PATTERN.finditer(normalized_answer)
                       if is_shareable_page_link(match.group())]
    quotes = [" ".join(quote.split()) for quote in
              re.findall(r'[“「"]([^”」"\n]{12,})[”」"]', answer)
              if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", quote)
              and not is_shareable_page_link(quote)]
    if len(normalized_answer) < window and not quotes:
        return False

    def strings(value: object):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for nested in value.values():
                yield from strings(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from strings(nested)

    for item in tool_items:
        if item.get("type") == "commandExecution":
            outputs = [item.get("aggregatedOutput")]
        elif item.get("type") == "mcpToolCall":
            outputs = [item.get("result")]
        else:
            continue
        for output in outputs:
            for source in strings(output):
                normalized_source = " ".join(source.split())
                if any(quote in normalized_source for quote in quotes):
                    return True
                if len(normalized_source) < window:
                    continue
                for offset in range(len(normalized_answer) - window + 1):
                    if any(start < offset + window and offset < end
                           for start, end in shareable_spans):
                        continue
                    if normalized_answer[offset:offset + window] in normalized_source:
                        return True
    return False
