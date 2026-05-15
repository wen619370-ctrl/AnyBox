# AnyBox · 万用沙盒

**本地 AI 工具箱宿主程序** — 用自然语言描述需求，LLM 自动生成并执行 Python 脚本，即时呈现结果。

```
你说："帮我批量重命名这 50 张图片，按拍摄日期排序"
AnyBox 自动生成脚本 → 沙盒执行 → 完成！
```

---

## ✨ 功能特性

- 🧠 **对话式工具创建** — 像聊天一样描述需求，AI 自动生成工具代码
- 🔒 **本地执行** — 工具脚本在本地沙盒运行，数据不出本机
- 🎨 **动态 UI** — 工具界面由 AI 自动生成，无需手写前端
- 📦 **依赖管理** — 自动检测并安装 Python 依赖（pip）
- 🗂️ **工具库** — 保存、管理、复用已创建的工具
- 🪟 **PySide6 桌面应用** — 现代化 Qt 界面，支持 Windows

---

## 🖼️ 界面预览

启动后经过三步初始化向导（配置 API Key），进入主界面：

| 对话创造区 (Chat View) | 工具库 (Tool Library) |
|---|---|
| 输入自然语言描述需求，AI 生成工具 | 浏览和管理已创建的工具 |

---

## 🚀 快速开始

### 1. 下载安装

从 [Releases](../../releases) 页面下载最新 `AnyBox.exe`，直接运行即可（无需安装 Python 环境）。

### 2. 配置 API Key

首次启动会进入向导，指引你获取并填写 DeepSeek API Key。（可以填其它的，但建议是用deepseek，因为开发者只测试了deepseek的）

### 3. 开始使用

在主界面对话框中描述你的需求，例如：
- *"帮我写一个压缩当前目录下所有 PNG 图片的工具"*
- *"创建一个 CSV 文件合并工具，支持选择多个文件"*
- *"生成一个 Markdown 阅读器，带实时预览"*

---

## 🛠️ 技术栈

| 组件 | 技术 |
|------|------|
| GUI 框架 | PySide6 (Qt for Python) |
| AI 模型 | DeepSeek Chat API |
| 打包部署 | PyInstaller + UPX |
| 版本管理 | Git + GitHub |

---

## 📁 项目结构

```
LogicForge/
├── main.py                  # 应用入口，生命周期管理
├── AnyBox.spec              # PyInstaller 打包配置
├── core/                    # 核心逻辑
│   ├── llm_controller.py    # LLM API 调用与工具代码生成
│   ├── executor.py          # 沙盒脚本执行器
│   └── dependency_manager.py # pip 依赖自动安装
├── ui/                      # 用户界面
│   ├── main_window/         # 主窗口（对话区、工具库、设置）
│   ├── wizard/              # 初始化向导（API 配置）
│   └── tool_executor/       # 动态 UI 构建与工具执行
├── utils/                   # 工具模块（日志等）
├── assets/                  # 图标资源
└── tests/                   # 测试脚本
```

---

## 🔧 开发

```bash
# 克隆仓库
git clone https://github.com/wen619370-ctrl/AnyBox.git
cd AnyBox

# 安装依赖
pip install -r requirements.txt

# 运行
python main.py

# 打包
python -m PyInstaller AnyBox.spec --clean --noconfirm
```

---

## 🤖 AI 代码声明

本项目约 **90% 的代码由 AI（DeepSeek Chat / Claude 等大语言模型）生成**，人类仅负责需求描述、代码审查、调试修正与架构指导。

具体而言：
- **项目结构设计** — 人工规划，AI 辅助细化
- **Python 源代码**（`core/`、`ui/`、`utils/` 等） — AI 生成，人工审查与修正
- **UI 布局与样式** — AI 生成 PySide6 界面代码
- **文档**（README、注释） — AI 生成，人工校订
- **打包配置**（`AnyBox.spec`） — AI 生成

这是一种 **"Vibe Coding"** 风格的开发实践——以自然语言驱动编码，将大语言模型作为主要代码生产力工具。

---

## 📄 License

本项目仅用于学习与研究目的。
