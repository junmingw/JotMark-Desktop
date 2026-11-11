"""Human-readable, best-effort explanations for project import failures."""

import errno
import json


def classify_import_failure(error, stage):
    """Return a category and advice without claiming an unverified cause."""
    winerror = getattr(error, "winerror", None)
    if stage == "准备项目":
        return "未选择图片项目", "请先点击“打开目录”，选择图片项目目录后再导入。"
    if winerror in (32, 33):
        return "文件可能正被占用", "请关闭正在编辑该文件的程序，暂停同步软件后重试。"
    if winerror == 206 or getattr(error, "errno", None) == errno.ENAMETOOLONG:
        return "文件路径过长", "将整个项目复制到较短的本地路径后重试；不要删除原项目。"
    if winerror == 112 or getattr(error, "errno", None) in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
        return "磁盘空间不足", "检查目标磁盘剩余空间和账户磁盘配额。"
    if isinstance(error, PermissionError) or winerror == 5 or getattr(error, "errno", None) in (errno.EACCES, errno.EPERM, errno.EROFS):
        return "没有所需权限或目录只读", "确认源文件可读取，项目目录和备份目录可写；检查安全软件和受控文件夹访问设置。"
    if isinstance(error, UnicodeDecodeError):
        return "文件编码不受支持", "请将文件另存为 UTF-8 后重试；属性 JSON 也支持带 BOM 的 UTF-16/UTF-32 和 GB18030。不要直接覆盖唯一原件。"
    if isinstance(error, json.JSONDecodeError):
        return "JSON 格式有误", f"请检查第 {error.lineno} 行、第 {error.colno} 列附近的逗号、引号和括号。"
    if isinstance(error, FileNotFoundError):
        return "文件或目录不存在", "检查文件是否被移动、网络磁盘是否在线，以及项目目录是否仍然存在。"
    if isinstance(error, IsADirectoryError):
        return "选择了文件夹而不是文件", "请选择具体的 TXT 类别文件或 JSON 属性配置文件。"
    if isinstance(error, FileExistsError):
        return "备份或目标路径已存在", "请查看目标路径并重试；不要删除已有备份。"
    if isinstance(error, RuntimeError) and any(word in str(error) for word in ("外部修改", "其他程序修改", "发生变化", "尚未读取", "禁止覆盖")):
        return "目标文件受到保护", "文件可能已被其他程序修改或未完整读取。请先备份，关闭其他编辑程序，再重新打开项目。"
    if isinstance(error, (ValueError, TypeError)):
        return "文件内容或结构不符合要求", "类别文件应为每行一个类别的 UTF-8 TXT；属性文件应为含 global 或 classes 的 JotMark 属性 JSON。"
    if isinstance(error, OSError):
        return "文件系统操作失败", "请根据下方原始错误检查路径、磁盘状态、权限和文件占用。"
    return "未能自动确定原因", "请将下方详细信息提供给维护者；分享前检查路径等敏感信息。"


def format_import_failure(kind, stage, error, source="", target="", backup=""):
    category, advice = classify_import_failure(error, stage)
    lines = [f"导入类型：{kind}", f"失败阶段：{stage}", f"错误归类：{category}", f"建议：{advice}"]
    if source:
        lines.append(f"源文件：{source}")
    if target:
        lines.append(f"目标路径：{target}")
    if backup:
        lines.append(f"备份路径：{backup}")
    lines.append(f"原始错误：{type(error).__name__}: {error}")
    for field in ("errno", "winerror"):
        value = getattr(error, field, None)
        if value is not None:
            lines.append(f"{field}: {value}")
    if stage in ("写入项目", "保存类别"):
        lines.append("写入可能已部分完成；请停止编辑并核对项目及备份，勿直接覆盖。")
    return "\n".join(lines)
