"""
LLMController — AI 大脑与工具持久化

核心职责：
    1. 封装 System Prompt，引导 AI 以对话方式逐步构建工具
    2. 调用 OpenAI 兼容 API（DeepSeek / Kimi / 自定义端点）
    3. 从 AI 回复中检测并提取工具 JSON（多轮对话模式）
    4. 容错处理：JSON 解析失败时引导 AI 重试
    5. 工具持久化：将合法 JSON 写入 tools/ 并更新索引
"""

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 路径常量 — 与 main.py 保持同源
# ---------------------------------------------------------------------------
def _get_app_root() -> Path:
    """返回应用程序根目录。

    开发环境：当前文件所在目录的上级目录 (项目根)。
    PyInstaller 打包后：exe 所在目录（而非临时解压目录 _MEIPASS）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT_DIR = _get_app_root()
TOOLS_DIR = ROOT_DIR / "tools"
TOOLS_INDEX = ROOT_DIR / "tools_config.json"

# ---------------------------------------------------------------------------
# Master System Prompt — 对话式工具构建助手
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = r"""
# Role
你是一个名为 **AnyBox** 的工具构建助手。你的任务是通过**多轮对话**理解用户需求，逐步完善工具规格，最终生成一个可被 AnyBox 宿主程序解析和执行的 **严格 JSON 数据结构**。

# 工作流程

## 第一阶段：需求澄清（对话模式）
当用户描述一个工具需求时，你可以：
1. 用**自然语言**回复，询问不清楚的细节（例如："输出到哪里？"、"需要支持哪些格式？"）
2. 在**工具逻辑/代码实现层面**给出建议或更优算法方案（如用多线程加速、用 hash 比较文件等）
3. 确认后总结你对需求的理解
4. **不要急于输出 JSON**——在需求明确之前，保持对话

## 第二阶段：工具生成（JSON 模式）
当你确认需求已经足够清晰时，在回复中**附带**一个被 ````json ... ```` 包裹的 JSON 对象。

你可以同时包含自然语言说明（如 "这是我根据你的需求生成的工具，你可以测试或继续修改"），**然后**附加 JSON 代码块。

JSON 结构必须包含以下三个顶层字段：

```json
{
  "meta": {
    "tool_id": "唯一时间戳（13位毫秒级，字符串）",
    "tool_name": "简洁的中文工具名称（≤20字）",
    "description": "工具功能的一句话描述（≤80字）"
  },
  "ui": [
    // 控件数组，详见下方 UI Schema 规范
  ],
  "code": "单文件 Python 代码（转义后的多行字符串）"
}
```

---

# UI Schema 规范

`ui` 字段是一个数组，每个元素描述一个用户输入控件。**支持的控件类型及格式如下**：

## 1. text_input — 单行文本输入
```json
{"id": "string(必填)", "type": "text_input", "label": "string(必填)", "default": "string(可选)", "placeholder": "string(可选)"}
```

## 2. folder_picker — 文件夹选择器
```json
{"id": "string(必填)", "type": "folder_picker", "label": "string(必填)"}
```

## 3. file_picker — 文件选择器
```json
{"id": "string(必填)", "type": "file_picker", "label": "string(必填)", "filter": "string(可选，如'图片文件 (*.jpg *.png)')"}
```

## 4. dropdown — 下拉选择器
```json
{"id": "string(必填)", "type": "dropdown", "label": "string(必填)", "options": ["选项1", "选项2", "..."]}
```

## 5. checkbox — 复选框
```json
{"id": "string(必填)", "type": "checkbox", "label": "string(必填)", "default": true或false}
```

## 6. multi_folder_picker — 多文件夹选择器（批量操作）
```json
{"id": "string(必填)", "type": "multi_folder_picker", "label": "string(必填)"}
```
支持三种交互方式：
- **单个添加**：点击「添加...」按钮选择一个文件夹
- **批量添加**：点击「批量添加...」按钮，循环弹窗可连续选择多个文件夹，点取消结束
- **拖拽添加**：从系统资源管理器直接拖拽文件夹到列表

最终返回一个包含所有已选文件夹路径的**字符串列表 (list[str])**。路径自动去重。
**当用户需求中出现"多个文件夹""批量选择文件夹""多选文件夹"等描述时，使用此控件。**

## 7. multi_file_picker — 多文件选择器（批量操作）
```json
{"id": "string(必填)", "type": "multi_file_picker", "label": "string(必填)", "filter": "string(可选，如'图片文件 (*.jpg *.png)')"}
```
支持两种交互方式：
- **多选添加**：点击「添加文件...」按钮弹出文件对话框，支持 Ctrl+点击 / Shift+框选 一次选择多个文件
- **拖拽添加**：从系统资源管理器直接拖拽文件到列表

最终返回一个包含所有已选文件路径的**字符串列表 (list[str])**。路径自动去重。
**当用户需求中出现"多个文件""多选文件""批量处理文件""拖拽文件"等描述时，使用此控件。**

## 8. label — 纯文本标签（用于显示提示信息，不参与数据收集）
```json
{"id": "string(可选)", "type": "label", "text": "string(必填)", "word_wrap": true或false}
```

**UI 设计准则**：
- 每个控件必须有唯一的 `id`
- 控件排列顺序即为界面展示顺序
- 仅创建用户真正需要填写的字段，避免冗余

---

# Code 规范

`code` 字段是一段 **纯逻辑的 Python 3 代码（转义的多行字符串）**。必须遵守以下铁律：

## 1. 禁止 GUI
代码中 **严禁包含任何 GUI 代码**（禁止导入 tkinter、PySide6、PyQt 等）。宿主程序提供 UI，脚本仅负责计算逻辑。

## 2. 输入获取
用户填写的参数通过 **环境变量 `ANYBOX_PARAMS`** 传递。脚本必须以如下方式读取输入：
```python
import os, json
params = json.loads(os.environ["ANYBOX_PARAMS"])
# 例如：input_dir = params["input_dir"]
```

## 3. 进度反馈
通过标准输出 (stdout) 打印特定格式的 JSON 来报告进度。格式为：
```
[[SYS_MSG]] {"__type__": "progress", "percent": 数值(0-100), "message": "当前步骤描述"}
```
完成时输出：
```
[[SYS_MSG]] {"__type__": "done", "output_dir": "输出文件夹的绝对路径"}
```
错误时输出：
```
[[SYS_MSG]] {"__type__": "error", "message": "错误描述"}
```

**重要**：每一行只能包含一条 `[[SYS_MSG]]`，且 `[[SYS_MSG]]` 必须是该行的起始字符串。

## 4. 库使用规范
- **优先使用 Python 标准库**（os, sys, json, pathlib, shutil, subprocess 等）
- 如果必须使用第三方库，在代码顶部以注释形式声明：
  ```python
  # pip: Pillow
  # pip: openpyxl
  from PIL import Image
  import openpyxl
  ```
  格式为 `# pip: 包名`，每行一个。宿主程序将在执行前自动安装这些依赖。

## 5. 代码必须自包含
- 所有函数和类定义在单文件内完成
- 文件路径从 `params` 中获取，不得硬编码
- 使用 `if __name__ == "__main__":` 作为入口保护（推荐）

---

# 核心原则
1. **先用自然语言对话，搞清需求后再生成 JSON**
2. 用户说 "再改一下"、"增加XX功能" 时，输出**更新后的完整 JSON**
3. UI 简洁实用，代码安全稳健
4. 你是一个助手，不是代码生成器——帮助用户思考，而不是盲目执行
"""

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logger = logging.getLogger("anybox.llm")


# ---------------------------------------------------------------------------
# LLMController
# ---------------------------------------------------------------------------
class LLMController:
    """
    OpenAI 兼容 API 的封装层。

    职责：
        1. 构造请求（嵌入 System Prompt）
        2. 发送 API 调用
        3. 解析响应中的 JSON 工具描述（多轮对话模式）
        4. 容错处理与工具持久化
    """

    def __init__(self, api_config: dict) -> None:
        """
        Args:
            api_config: {
                "base_url": "https://api.deepseek.com/v1",
                "api_key":  "sk-xxx",
                "model":    "deepseek-v4-flash",
                "temperature": 0.3,      # 可选，默认 0.3
                "max_tokens":  4096,      # 可选，默认 4096
                "thinking": True,         # 可选，是否启用思考模式 (DeepSeek v4)
                "reasoning_effort": "high",  # 可选，思考强度 high|max (DeepSeek v4)
            }
        """
        self._base_url = api_config["base_url"].rstrip("/")
        self._api_key = api_config["api_key"]
        self._model = api_config.get("model", "deepseek-v4-flash")
        self._temperature = api_config.get("temperature", 0.3)
        self._max_tokens = api_config.get("max_tokens", 4096)
        self._thinking = api_config.get("thinking", True)
        self._reasoning_effort = api_config.get("reasoning_effort", "high")

    # ------------------------------------------------------------------
    # 公开 API：多轮对话模式（推荐）
    # ------------------------------------------------------------------
    def chat_with_tool_detection(
        self, user_input: str, history: list[dict] | None = None
    ) -> dict:
        """
        发送对话请求，并从 AI 回复中检测是否包含工具 JSON。

        这是多轮对话模式的核心方法。AI 可以返回纯文本、或者"文本 + JSON"混合。

        Returns:
            {
              "ok": True,
              "text": "AI 的自然语言回复（始终存在）",
              "tool_json": {meta, ui, code} | None,  # None = 纯对话，无工具
              "raw": "AI 原始完整回复",
            }
            网络错误等：
            {
              "ok": False,
              "text": "错误描述",
              "tool_json": None,
              "raw": "",
            }
        """
        logger.info(
            "chat_with_tool_detection 开始 | user_input_len=%d | history_rounds=%d",
            len(user_input),
            len(history or []) // 2,
        )
        try:
            raw_reply = self.chat(user_input, history)
        except RuntimeError as e:
            logger.warning("LLM chat 异常 | error=%s", e)
            return {"ok": False, "text": str(e), "tool_json": None, "raw": ""}

        logger.debug("收到 LLM 回复 | len=%d | preview=%.200s...", len(raw_reply), raw_reply)

        # 尝试从回复中提取工具 JSON
        tool_json = _extract_json(raw_reply)

        parse_warning = ""
        if tool_json is not None:
            # 补充 tool_id
            if not tool_json.get("meta", {}).get("tool_id"):
                tool_json.setdefault("meta", {})["tool_id"] = str(int(time.time() * 1000))
            logger.info(
                "检测到工具 JSON | tool_name=%s | ui_controls=%d | code_len=%d",
                tool_json["meta"].get("tool_name"),
                len(tool_json.get("ui", [])),
                len(tool_json.get("code", "")),
            )
        else:
            logger.debug("未检测到工具 JSON，作为纯对话处理")
            # 仅当 AI 明确输出了 ```json 代码围栏但解析失败时，才提示用户格式异常
            # （避免误判"生成"等对话中的普通词汇）
            if "```json" in raw_reply and not parse_warning:
                parse_warning = (
                    "⚠️ AI 输出了 JSON 代码块，但格式无法被宿主程序解析。\n"
                    "请直接点击发送按钮（无需输入新内容），让 AI 重新生成完整的 "
                    "```json ... ``` 代码块。"
                )
                logger.debug("检测到 ```json 围栏但提取失败 | 触发 parse_warning")

        # 分离文本：移除 JSON 代码块，只保留自然语言部分
        text = _strip_json_fence(raw_reply)

        return {
            "ok": True,
            "text": text.strip() or "(空回复)",
            "tool_json": tool_json,
            "raw": raw_reply,
            "parse_warning": parse_warning,
        }

    # ------------------------------------------------------------------
    # 公开 API：纯对话（无工具检测）
    # ------------------------------------------------------------------
    def chat(self, user_input: str, history: list[dict] | None = None) -> str:
        """
        发送对话请求，返回 AI 的原始文本回复。

        使用 QNetworkAccessManager + QEventLoop 实现同步阻塞调用，
        彻底解决 urllib.request 受 Windows 系统代理干扰而假死的问题。

        Args:
            user_input: 用户当前输入
            history:    历史消息列表 [{"role":"user"|"assistant","content":"..."}, ...]

        Returns:
            AI 回复的纯文本内容

        Raises:
            RuntimeError: 网络或 API 错误
        """
        from PySide6.QtNetwork import (
            QNetworkAccessManager,
            QNetworkRequest,
            QNetworkReply,
            QSslSocket,
        )
        from PySide6.QtCore import QEventLoop, QUrl, QTimer

        messages = self._build_messages(user_input, history or [])

        url = QUrl(f"{self._base_url}/chat/completions")
        # 构造请求体
        request_body: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }

        # DeepSeek v4 思考模式：通过 extra_body 传入 thinking 参数
        if self._thinking:
            request_body["reasoning_effort"] = self._reasoning_effort
            request_body["extra_body"] = {"thinking": {"type": "enabled"}}

        body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")

        logger.info(
            "LLM API 请求开始 | model=%s | url=%s | body_size=%d bytes",
            self._model,
            url.toString(),
            len(body),
        )

        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        req.setRawHeader(b"Authorization", f"Bearer {self._api_key}".encode("utf-8"))

        # SSL：容忍自签名/企业 CA
        ssl_config = req.sslConfiguration()
        ssl_config.setPeerVerifyMode(QSslSocket.PeerVerifyMode.VerifyNone)
        req.setSslConfiguration(ssl_config)

        nam = QNetworkAccessManager()
        nam.setTransferTimeout(120_000)  # 120 秒总超时

        reply = nam.post(req, body)

        loop = QEventLoop()

        # 兜底看门狗
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(loop.quit)
        watchdog.start(125_000)

        result_data: dict | None = None
        result_error: str | None = None

        def on_finished() -> None:
            nonlocal result_data, result_error
            watchdog.stop()

            error = reply.error()
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            logger.debug(
                "LLM 网络响应到达 | http_status=%s | error=%s | error_string=%s",
                status,
                error,
                reply.errorString(),
            )

            if error == QNetworkReply.NetworkError.NoError and status == 200:
                try:
                    raw = bytes(reply.readAll()).decode("utf-8")
                    result_data = json.loads(raw)
                    usage = result_data.get("usage", {})
                    logger.info(
                        "LLM 响应解析成功 | response_size=%d bytes | "
                        "tokens_prompt=%s | tokens_completion=%s | tokens_total=%s",
                        len(raw),
                        usage.get("prompt_tokens"),
                        usage.get("completion_tokens"),
                        usage.get("total_tokens"),
                    )
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    result_error = f"API 响应解析失败: {e}"
                    logger.error("LLM 响应 JSON 解析失败: %s", e)
            elif status in (401, 403):
                result_error = f"API Key 无效或权限不足 (HTTP {status})"
                logger.error("LLM 认证失败 | http_status=%s", status)
            elif error == QNetworkReply.NetworkError.OperationCanceledError:
                result_error = "请求被中断（可能超时），请检查网络"
                logger.error("LLM 请求被中断（操作取消）")
            elif error == QNetworkReply.NetworkError.TimeoutError:
                result_error = "请求超时（120 秒），请检查网络或关闭系统代理后重试"
                logger.error("LLM 请求超时")
            else:
                detail = reply.errorString()
                result_error = f"网络请求失败 (HTTP {status}): {detail}"
                logger.error(
                    "LLM 网络请求失败 | http_status=%s | error=%s | detail=%s",
                    status,
                    error,
                    detail,
                )

            loop.quit()

        reply.finished.connect(on_finished)
        logger.debug("LLM 进入事件循环等待（阻塞后台线程）...")
        loop.exec()

        reply.deleteLater()
        nam.deleteLater()

        if result_error is not None:
            logger.error(result_error)
            raise RuntimeError(result_error)

        if result_data is None:
            raise RuntimeError("API 请求失败：无响应数据")

        try:
            message = result_data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"API 响应结构异常: {e}") from e

        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""

        # DeepSeek v4 思考模式容错：
        # 如果 max_tokens 不足，模型可能将全部配额用于 thinking，
        # 导致 content 为空。此时降级展示 reasoning 摘要 + 提示。
        if not content.strip():
            if reasoning:
                logger.warning(
                    "思考模式 tokens 耗尽（思考=%d 字符, content=空），降级展示推理摘要",
                    len(reasoning),
                )
                # 取推理尾部 2000 字作为可见回复
                tail = reasoning[-2000:] if len(reasoning) > 2000 else reasoning
                content = (
                    "⚠️ 思考过程使用了全部 Token，未能生成最终回复。\n\n"
                    f"[思考摘要]\n{tail}\n\n"
                    "───\n"
                    "💡 提示：请在 API 设置中增大 Max Tokens（建议 ≥ 16384），"
                    "或关闭思考模式。"
                )
            else:
                logger.warning("LLM 返回空 content 且无 reasoning_content")
                content = ""

        logger.debug(f"LLM 原始回复 (前200字符): {content[:200]}")
        return content

    # ------------------------------------------------------------------
    # 公开 API：旧版一次性生成方法（保留向后兼容）
    # ------------------------------------------------------------------
    def generate_tool(
        self, user_input: str, history: list[dict] | None = None
    ) -> dict:
        """
        一站式工具生成：调用 LLM → 提取 JSON → 容错处理。

        这是旧版单轮方法，保留向后兼容。新代码推荐使用 chat_with_tool_detection()。

        Returns:
            成功：{"ok": True,  "data": {meta, ui, code}, "raw": "AI 原始回复"}
            失败：{"ok": False, "error": "错误描述", "raw": "AI 原始回复"}
        """
        logger.info(
            "generate_tool 开始 | user_input_len=%d | history_rounds=%d",
            len(user_input),
            len(history or []) // 2,
        )
        try:
            raw_reply = self.chat(user_input, history)
        except RuntimeError as e:
            logger.warning("generate_tool 失败（chat 异常） | error=%s", e)
            return {"ok": False, "error": str(e), "raw": ""}

        logger.debug("generate_tool 收到 LLM 回复 | len=%d", len(raw_reply))
        parsed = _extract_json(raw_reply)

        if parsed is None:
            logger.warning(
                "generate_tool JSON 提取失败 | raw_preview=%.200s...", raw_reply
            )
            return {
                "ok": False,
                "error": (
                    "AI 返回的内容无法解析为有效的工具 JSON。\n"
                    "请重新描述需求，提示词可以更具体一些"
                    "（如：'请创建一个批量重命名文件的工具，需要选择文件夹和输入新文件名前缀'）。"
                ),
                "raw": raw_reply,
            }

        if not parsed.get("meta", {}).get("tool_id"):
            parsed.setdefault("meta", {})["tool_id"] = str(int(time.time() * 1000))

        logger.info(
            "generate_tool 成功 | tool_name=%s | ui_controls=%d | code_len=%d",
            parsed["meta"].get("tool_name"),
            len(parsed.get("ui", [])),
            len(parsed.get("code", "")),
        )
        return {"ok": True, "data": parsed, "raw": raw_reply}

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    def _build_messages(
        self, user_input: str, history: list[dict]
    ) -> list[dict]:
        """
        构造完整的 messages 数组：
            [system_prompt, ...history, user_input]

        最多保留最近 10 轮历史（20 条消息），防止 token 溢出。
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        max_history = 20
        recent = history[-max_history:] if len(history) > max_history else history

        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_input})
        return messages


# ===================================================================
# JSON 提取器
# ===================================================================

_RE_FENCED_JSON = re.compile(r"```json\s*?\n?(.*?)```", re.DOTALL | re.IGNORECASE)
_RE_RAW_JSON_OBJ = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    """
    从 AI 回复文本中提取合法 JSON 工具描述。

    提取策略（按优先级）：
        1. 定位 ```json ... ``` 围栏代码块
        2. 兜底：定位最外层 { ... } 对象
        3. 终极兜底：如果以上都失败但文本包含 meta/ui/code 三大特征字段，
           尝试以 "code" 字段字符串末尾为锚点手动闭合截断的 JSON
        4. 解析并验证必需字段 (meta.tool_name, ui, code)

    Returns:
        解析成功的 dict，或 None
    """
    if not text:
        logger.debug("JSON 提取 | 输入为空 → 返回 None")
        return None

    candidates: list[str] = []
    last_parse_error: str = ""

    # 策略 1：查找围栏代码块（支持 ```json 同行和分行两种写法，也支持裸 ``` 封闭）
    fenced_matches = _RE_FENCED_JSON.findall(text)
    # 额外尝试裸 ``` 围栏（某些 LLM 可能忘记写 json 标记）
    if not fenced_matches:
        _RE_BARE_FENCE = re.compile(r"```\s*?\n?(.*?)```", re.DOTALL)
        bare_matches = _RE_BARE_FENCE.findall(text)
        # 只保留看起来像 JSON 的裸围栏内容
        for m in bare_matches:
            stripped = m.strip()
            if stripped and stripped[0] == '{' and stripped[-1] == '}':
                fenced_matches.append(stripped)
    for match in fenced_matches:
        stripped = match.strip() if isinstance(match, str) else str(match).strip()
        if stripped:
            candidates.append(stripped)

    logger.debug(
        "JSON 提取策略1(围栏) | 找到 %d 个候选项 | sizes=%s",
        len(candidates),
        [len(c) for c in candidates] if candidates else "无",
    )

    # 策略 2：兜底 —— 定位最外层 { }
    if not candidates:
        logger.debug("JSON 提取策略2(花括号) | 无围栏候选项，兜底尝试定位最外层对象")
        first_brace = text.find("{")
        if first_brace != -1:
            depth = 0
            end = -1
            for i in range(first_brace, len(text)):
                ch = text[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end != -1:
                candidate = text[first_brace : end + 1]
                candidates.append(candidate)
                logger.debug(
                    "JSON 提取策略2(花括号) | 找到闭合对象 | first=%d end=%d len=%d",
                    first_brace, end, len(candidate),
                )
            else:
                logger.debug("JSON 提取策略2(花括号) | 花括号未闭合 (最终深度=%d)", depth)
        else:
            logger.debug("JSON 提取策略2(花括号) | 文本中未找到 '{'")

    # 策略 3：终极兜底 —— 如果仍未找到闭合花括号，但文本包含工具 JSON
    # 的三大特征字段，尝试以 "code" 字段字符串末尾为锚点手动闭合
    if not candidates and _looks_like_tool_json(text):
        logger.warning(
            "JSON 提取策略3(终极兜底) | 围栏和花括号均失败，"
            "但检测到 JSON 特征指标 → 尝试截断修复"
            " | text_preview=%.120s...", text[:120]
        )
        repaired = _try_close_truncated_json(text)
        if repaired is not None:
            candidates.append(repaired)
            logger.debug("JSON 提取终极兜底 | 截断修复成功 | repaired_len=%d", len(repaired))
        else:
            logger.debug("JSON 提取终极兜底 | 截断修复失败")

    for idx, candidate in enumerate(candidates):
        try:
            parsed = json.loads(candidate)
            logger.debug("JSON 候选项解析 | idx=%d parse_ok=True len=%d", idx, len(candidate))
        except json.JSONDecodeError as e:
            last_parse_error = str(e)
            logger.debug(
                "JSON 候选项解析 | idx=%d parse_ok=False | 错误位置=%s",
                idx, _format_json_error(e, candidate),
            )
            repaired = _repair_json(candidate)
            if repaired is None:
                logger.debug("JSON 候选项修复 | idx=%d 放弃（无变更）", idx)
                continue
            try:
                parsed = json.loads(repaired)
                logger.debug("JSON 候选项修复 | idx=%d 修复后解析成功", idx)
            except json.JSONDecodeError as e2:
                last_parse_error = str(e2)
                # 第三层兜底：尝试闭合截断的 JSON（如 code 字段值被截断）
                logger.debug(
                    "JSON 候选项修复 | idx=%d 常规修复仍失败 → 尝试截断闭合",
                    idx,
                )
                closed = _try_close_truncated_json(repaired)
                if closed is None:
                    logger.debug("JSON 候选项截断闭合 | idx=%d 失败（无锚点）", idx)
                    continue
                try:
                    parsed = json.loads(closed)
                    logger.debug("JSON 候选项截断闭合 | idx=%d 闭合后解析成功", idx)
                except json.JSONDecodeError as e3:
                    last_parse_error = str(e3)
                    logger.debug(
                        "JSON 候选项截断闭合 | idx=%d 闭合后仍失败", idx,
                    )
                    continue

        if _validate_tool_json(parsed):
            logger.info(
                "JSON 提取成功 | tool_name=%s | ui_controls=%d | code_len=%d",
                parsed["meta"].get("tool_name", "?"),
                len(parsed.get("ui", [])),
                len(parsed.get("code", "")),
            )
            return parsed
        else:
            logger.debug("JSON 候选项验证 | idx=%d valid=False | 缺少 meta.tool_name / ui 数组 / code 字符串", idx)

    logger.warning(
        "JSON 提取失败 | 共 %d 个候选项全部不合法 | parse_error=%s | text_preview=%.80s...",
        len(candidates), last_parse_error, text[:80],
    )
    return None


def _format_json_error(err: json.JSONDecodeError, text: str) -> str:
    """格式化 JSON 解析错误，附带错误附近的文本片段供调试。"""
    start = max(0, err.pos - 40)
    end = min(len(text), err.pos + 40)
    snippet = text[start:end].replace("\n", "\\n")
    return f"行{err.lineno}列{err.colno}: {err.msg} | 附近: ...{snippet}..."


def _validate_tool_json(data: dict) -> bool:
    """校验 JSON 是否包含 AnyBox 协议的三个必需顶层字段。"""
    if not isinstance(data, dict):
        return False
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return False
    if not meta.get("tool_name"):
        return False
    ui = data.get("ui")
    if not isinstance(ui, list) or len(ui) == 0:
        return False
    code = data.get("code")
    if not isinstance(code, str) or len(code.strip()) == 0:
        return False
    return True


def _repair_json(text: str) -> str | None:
    """尝试修复常见 JSON 格式错误。

    修复策略：
        1. 移除 JavaScript 风格的行注释 (// ...)
        2. 移除 Python 风格的行注释 (# ...)
        3. 移除尾随逗号（如 {"a": 1,} → {"a": 1}）
        4. 尝试修复 code 字段内未转义的控制字符（\\n → \\\\n 等）

    Returns:
        修复后的 JSON 文本，若无需修复则返回 None（调用方据此决定是否用原文本重试）。
    """
    original = text

    # 策略 1 & 2：移除行注释
    lines = text.split("\n")
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            continue
        cleaned.append(line)
    text = "\n".join(cleaned)

    # 策略 3：移除尾随逗号
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 策略 4：修复 code 字段中未转义的实际换行符
    # 某些 LLM 在 JSON 字符串字面量中直接嵌入了真实换行而非 \\n，
    # 这会导致 json.loads 失败。这里尝试将 code 值中的换行替换为 \\n
    text = _fix_code_field_escapes(text)

    if text != original:
        return text
    return None


def _fix_code_field_escapes(text: str) -> str:
    """修复 'code' 字段值中未转义的控制字符。

    仅当文本中包含 '"code"' 字段且字段值以双引号字符串形式存在时才处理。
    在 JSON 字符串值内部，将真实的 \\n 替换为 \\\\n，
    真实 \\t 替换为 \\\\t，真实 \\r 替换为 \\\\r。
    """
    # 定位 "code": " 起始位置
    code_start = re.search(r'"code"\s*:\s*"', text)
    if not code_start:
        return text

    # 找到 code 字符串值的结束引号（未转义的 "）
    value_start = code_start.end()
    value_end = _find_last_unescaped_quote(text, value_start)
    if value_end is None or value_end <= value_start:
        return text

    prefix = text[:value_start]
    value = text[value_start:value_end]
    suffix = text[value_end:]

    # 仅当值中包含实际换行符时才修复
    if "\n" not in value and "\t" not in value:
        return text

    # 转义控制字符（但保留已转义的 \\n 不动）
    # 策略：先标记已转义的 \\n，替换未转义的 \\n，再还原
    value = value.replace("\\n", "\x00")   # 占位已转义
    value = value.replace("\n", "\\n")     # 转义真实换行
    value = value.replace("\x00", "\\n")   # 还原已转义

    value = value.replace("\\t", "\x01")   # 占位已转义
    value = value.replace("\t", "\\t")     # 转义真实制表符
    value = value.replace("\x01", "\\t")   # 还原已转义

    value = value.replace("\\r", "\x02")   # 占位已转义
    value = value.replace("\r", "\\r")     # 转义真实回车
    value = value.replace("\x02", "\\r")   # 还原已转义

    return prefix + value + suffix


def _looks_like_tool_json(text: str) -> bool:
    """快速启发式检查：文本是否可能是一个被截断或格式异常的 AnyBox 工具 JSON。"""
    indicators = ('"meta"', '"ui"', '"code"', '"tool_name"', '"tool_id"')
    return sum(1 for kw in indicators if kw in text) >= 3


def _try_close_truncated_json(text: str) -> str | None:
    """
    尝试修复被截断的 JSON：定位最后一个 "code" 字段值，在其后补全 "}。
    仅当文本具有明确的 meta/ui/code 结构时才尝试。
    """
    # 找到 "code": 后面的字符串起始位置
    code_match = re.search(r'"code"\s*:\s*"', text)
    if not code_match:
        return None
    # 从 code 字符串起始位置开始，找到字符串结束的未转义双引号
    code_start = code_match.end()
    # 在 code 字段值之后尝试定位闭合
    # 简单策略：找到最后一个未转义的 " 作为 code 值的结束，
    # 然后补上 } 闭合整个 JSON
    last_quote = _find_last_unescaped_quote(text, code_start)
    if last_quote is None:
        return None
    # 在最后一个引号后补上 }
    return text[:last_quote + 1] + "}"


def _find_last_unescaped_quote(text: str, start: int) -> int | None:
    """从 start 位置开始，找到文本中最后一个未转义的双引号位置。"""
    last_valid = None
    i = start
    while i < len(text):
        if text[i] == '"':
            # 检查前面是否有奇数个反斜杠（转义）
            backslash_count = 0
            j = i - 1
            while j >= 0 and text[j] == '\\':
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:  # 偶数 = 未转义
                last_valid = i
        i += 1
    return last_valid


def _strip_json_fence(text: str) -> str:
    """
    从 AI 回复中移除 ```json ... ``` 围栏代码块和 JSON 对象，
    只保留自然语言部分。
    """
    # 移除 ```json ... ``` 代码块
    cleaned = _RE_FENCED_JSON.sub("", text)
    # 移除行内的 `````` 标记（可能残留）
    cleaned = re.sub(r"```\w*\n?", "", cleaned)
    # 尝试移除纯 JSON 对象（兜底：如果文本中有一个大的 JSON 块）
    # 这里保守处理，不去动可能包含花括号的自然语言
    return cleaned.strip()


# ===================================================================
# 工具持久化
# ===================================================================
def save_tool(json_data: dict, user_prompt: str = "") -> str:
    """
    将 AI 生成的工具 JSON 持久化到磁盘。

    文件结构：
        tools/
        └── {tool_id}/
            ├── config.json    ← meta + ui
            └── script.py      ← 纯逻辑代码

    同时更新根目录的 tools_config.json 索引。

    Args:
        json_data:   AI 返回的工具 JSON {meta, ui, code}
        user_prompt: 用户当初创建工具时使用的自然语言需求（可选）

    Returns:
        tool_id (str)
    """
    meta = json_data["meta"]
    ui = json_data["ui"]
    code = json_data["code"]

    tool_id = meta["tool_id"]
    tool_name = meta.get("tool_name", "未命名工具")
    tool_dir = TOOLS_DIR / tool_id

    logger.info(
        "开始保存工具 | tool_id=%s | tool_name=%s | ui_controls=%d | code_len=%d | user_prompt_len=%d",
        tool_id, tool_name, len(ui), len(code), len(user_prompt),
    )

    # 1. 创建目录
    tool_dir.mkdir(parents=True, exist_ok=True)
    logger.debug("工具目录已创建 | path=%s", tool_dir)

    # 2. 写入脚本
    script_path = tool_dir / "script.py"
    _write_text(script_path, code)
    logger.debug("脚本文件已写入 | path=%s | size=%d bytes", script_path, len(code.encode("utf-8")))

    # 3. 写入配置
    config_path = tool_dir / "config.json"
    config_data = {"meta": meta, "ui": ui}
    _write_json(config_path, config_data)
    logger.debug("配置文件已写入 | path=%s | ui_controls=%d", config_path, len(ui))

    # 4. 更新索引
    _update_index(tool_id, meta, user_prompt)
    logger.debug("索引文件已更新 | tool_id=%s | tool_name=%s", tool_id, tool_name)

    logger.info("工具保存完成 | tool_id=%s | path=%s", tool_id, tool_dir)
    return tool_id


def _write_text(path: Path, content: str) -> None:
    """写入 UTF-8 文本文件，确保目录存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    """写入格式化的 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _update_index(tool_id: str, meta: dict, user_prompt: str = "") -> None:
    """更新 tools_config.json 索引文件。"""
    if TOOLS_INDEX.exists():
        try:
            index = json.loads(TOOLS_INDEX.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            index = {}
    else:
        index = {}

    index.setdefault("tools", {})[tool_id] = {
        "tool_name": meta.get("tool_name", "未命名工具"),
        "description": meta.get("description", ""),
        "user_prompt": user_prompt,
        "created_at": time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(
                int(tool_id[:10]) if tool_id.isdigit() else time.time()
            ),
        ),
    }

    _write_json(TOOLS_INDEX, index)
