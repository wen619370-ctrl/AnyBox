"""
演示脚本：模拟进度条输出，用于测试 ScriptExecutor。

此脚本不依赖任何第三方库，仅使用 Python 标准库。
运行时通过 stdout 输出 [[SYS_MSG]] 协议消息。
"""

import json
import os
import sys
import time


def emit(msg: dict) -> None:
    """将 dict 序列化为 [[SYS_MSG]] 前缀的 JSON 行输出到 stdout。"""
    raw = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
    print(f"[[SYS_MSG]]{raw}", flush=True)


def main() -> None:
    # ---- 读取宿主注入的参数 ----
    params_raw = os.environ.get("ANYBOX_PARAMS", "{}")
    try:
        params = json.loads(params_raw)
    except json.JSONDecodeError:
        params = {}

    total_steps = int(params.get("steps", 10))
    step_interval = float(params.get("interval", 0.3))

    # ---- 普通 print（应被 ScriptExecutor 归类为 tool_output_raw） ----
    print(f"开始模拟任务，共 {total_steps} 步")

    for i in range(1, total_steps + 1):
        time.sleep(step_interval)

        # 协议进度消息
        progress = {
            "__type__": "progress",
            "current": i,
            "total": total_steps,
            "percent": round(i / total_steps * 100, 1),
            "message": f"正在处理第 {i}/{total_steps} 项...",
        }
        emit(progress)

        # 再发一条普通 log
        print(f"DEBUG: 步骤 {i} 完成", flush=True)

    # ---- 任务完成 ----
    done_info = {
        "__type__": "done",
        "output_dir": os.path.join(os.getcwd(), "demo_output"),
        "files_generated": total_steps,
    }
    emit(done_info)

    # ---- 模拟写入输出目录 ----
    output_dir = done_info["output_dir"]
    os.makedirs(output_dir, exist_ok=True)
    for i in range(total_steps):
        with open(os.path.join(output_dir, f"file_{i+1}.txt"), "w") as f:
            f.write(f"Mock output file {i+1}\n")

    print("脚本执行完毕", flush=True)


if __name__ == "__main__":
    main()