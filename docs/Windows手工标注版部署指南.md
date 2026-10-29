# JotMark Windows 手工标注版部署指南

本指南适用于 **Windows 10 或 Windows 11 64 位电脑**，面向没有编程经验的使用者。完成后可使用矩形、多边形、折线、关键点和旋转框进行**纯手工标注**。

这个版本不安装、不下载、也不会加载 PyTorch、SAM2、SAM3、YOLO、CUDA、模型权重或训练平台。因此不需要 NVIDIA 显卡，普通办公电脑即可使用。

## 1. 收到压缩包后先做什么

1. 将 `JotMark-手工标注版-Windows.zip` 保存到“下载”文件夹。
2. 右键压缩包，选择 **全部提取**。
3. 建议把提取后的 `JotMark-Desktop` 文件夹移动到：

```text
C:\JotMark-Desktop
```

不要放在 U 盘、桌面同步盘或 OneDrive 自动同步目录中。程序和图片数据也尽量避免中文、空格或特殊符号过多的路径。新版安装脚本会把 Python 虚拟环境放在 `%LOCALAPPDATA%\JotMark\manual-venv` 的较短路径中，以规避 PySide6 的 Windows 长路径问题；但项目本身仍建议放在 `C:\JotMark-Desktop` 或 `E:\JotMark-Desktop` 这类简短路径。

解压后的目录顶层应有两个 Windows 批处理文件：

```text
Install-JotMark-Manual-Windows.bat
Start-JotMark-Manual-Windows.bat
```

如果资源管理器不显示 `.bat` 后缀，仍可通过文件名前的 `Install-JotMark...`、`Start-JotMark...` 识别它们。

## 2. 一键安装（推荐，无需预先安装 Python）

只要电脑能联网，直接双击：

```text
Install-JotMark-Manual-Windows.bat
```

脚本会先检查 Python；如果未安装，会自动使用 Windows Package Manager（`winget`）安装 Python 3.12。安装依赖前，脚本会对清华 TUNA、中科大、阿里云和官方 PyPI 的 HTTPS 响应时间进行短时测速，并选用当前响应最快的可用源；测速均失败或所选镜像下载失败时，会回退到官方 PyPI。该选择只用于本次安装，不会修改电脑的全局 pip 设置。整个过程不需要安装 CUDA、显卡驱动或模型。

极少数电脑会在 Python 刚安装完成后，当前黑色窗口仍找不到新的 Python 启动器。这不代表 Python 安装失败：关闭该窗口，再双击一次 `Install-JotMark-Manual-Windows.bat` 即可继续。

也可以在 PowerShell 中执行一条命令。假设项目解压到 `C:\JotMark-Desktop`：

```powershell
Set-Location C:\JotMark-Desktop; cmd /c .\Install-JotMark-Manual-Windows.bat
```

首次运行时保持黑色窗口开启，直到看到 `Installation complete.`。之后只需双击 `Start-JotMark-Manual-Windows.bat` 启动。

## 3. `winget` 不可用时：手动安装 Python

JotMark 手工版首次安装需要联网下载 Python 图形与图像处理组件。

### 方法 A：使用 Windows 终端安装

1. 在 Windows 开始菜单搜索 **终端** 或 **Windows Terminal**，右键选择“以普通方式打开”。
2. 复制并执行：

```powershell
winget install -e --id Python.Python.3.12
```

3. 安装完成后，关闭终端，再重新打开一个终端窗口，执行：

```powershell
py -3 --version
```

只要显示 Python 3.10 或更高版本即可。

### 方法 B：图形安装程序

如果 `winget` 不可用，请在浏览器搜索“Python Windows 下载”，从 Python 官方网站下载 **Windows installer (64-bit)**。安装时务必勾选：

```text
Add python.exe to PATH
```

然后选择 **Install Now**。安装结束后，重新打开终端并执行：

```powershell
py -3 --version
```

如果仍然提示找不到 `py` 或 `python`，请重启电脑后再试；仍无效时重新安装 Python，并确认勾选了上述 PATH 选项。

## 4. 安装 JotMark 手工版（脚本未自动完成时）

1. 打开 `JotMark-Desktop` 文件夹。
2. 双击 **Install-JotMark-Manual-Windows.bat**。
3. 若 Windows Defender SmartScreen 显示“Windows 已保护你的电脑”：
   - 先确认该 ZIP 确实来自可信发送者；
   - 点击“更多信息”；
   - 点击“仍要运行”。
4. 出现黑色窗口后请等待，不要关闭。它会在当前用户的本地应用数据目录中创建 `JotMark\manual-venv` 环境并下载依赖，不会在项目目录中写入庞大的 Python 依赖。
5. 看到 `Installation complete.` 后按任意键关闭窗口。

若安装失败，先检查网络是否可用，再重新双击安装文件。请不要删除 `%LOCALAPPDATA%\JotMark\manual-venv`，除非需要完全重新安装。

## 5. 每次启动

双击项目根目录的：

```text
Start-JotMark-Manual-Windows.bat
```

打开的黑色窗口必须保持开启；关闭 JotMark 后，它会自动结束。程序以最大化窗口启动。

启动后看不到“智能”“SAM”“模型”“训练”等按钮是正常的：这说明当前处于无模型、纯手工标注版。

### 建立桌面快捷方式（可选）

右键 `Start-JotMark-Manual-Windows.bat`，选择 **显示更多选项 → 发送到 → 桌面（创建快捷方式）**。以后双击桌面快捷方式即可启动。

## 6. 第一次标注的完整操作

### 6.1 整理图片

将要标注的图片放入独立文件夹，例如：

```text
D:\AnnotationProjects\ProjectA\images
```

图片可以是 JPG、PNG、BMP、WebP、TIFF 或 GIF。重要项目不要直接在 U 盘上标注；应先复制到本机磁盘。

### 6.2 打开图片目录

在程序左上角点击 **打开目录**，选择图片所在的文件夹。右侧“文件列表”默认仅显示图片名称；点击标题旁的 **后缀** 可显示或隐藏 `.jpg`、`.png` 等扩展名。点击文件名可切换图片。

### 6.3 添加类别并绘制

先在右侧最大的类别区域中建立类别，例如 `person`、`car`、`defect`，再选择左侧工具绘制。类别操作按钮下方有独立的长输入框，适合输入和检查较长的英文类别名称；输入后按 Enter 或点击 **+** 即可添加。

新建图片项目时，如果图片目录中还没有 `classes.txt`，程序会自动加载并保存默认的 `person`、`car`、`cyclist` 三个类别。默认模板位于项目目录：

```text
examples\classes.example.txt
```

该文件是普通 UTF-8 文本，一行一个类别；以 `#` 开头的行是说明，不会作为类别加载。可以复制它并按自己的项目改写，然后点击右侧类别区的 **导入**：未开始标注时可选择“替换当前类别”或“追加类别”；已经存在标注时只能追加，以避免 YOLO 类别编号错位。

右键点击一个类别，可选择 **重命名类别**、**管理类别属性** 或 **删除类别及其全部标注**。删除需要二次确认：只有点击 **我已确认，继续删除** 才会继续。它会删除当前图片中该类别的全部标注、该类别专属属性，并同步清理当前标注输出目录中的 JSON、XML、YOLO 标注。YOLO 中排在其后的类别编号会自动前移；删除前请确认已备份数据。

类别下的每个具体标注行可直接左键选中，画布与“标注属性”会同步到该标注。右键具体标注可修改类别或删除；删除单个标注会出现简洁确认框。也可在 **选择/编辑（V）** 模式下，把具体标注拖到另一类别下更换类别。

| 工具 | 操作 |
| --- | --- |
| 选择/编辑（V） | 选择、移动、调整已有标注。 |
| 矩形（R） | 按住鼠标左键拖动。 |
| 多边形（P） | 单击添加顶点，双击或 Enter 闭合。 |
| 折线（L） | 单击添加节点，双击、Enter 或右键结束。 |
| 关键点（T） | 按当前骨架模板放置关键点。 |
| 旋转框（O） | 拖动绘制；选中后可用 `Z/V` 每次旋转 5°、`X/C` 每次旋转 1°。 |

常用快捷键：`Ctrl + S` 保存、`Ctrl + Z` 撤销、`Ctrl + Shift + Z` 重做、`Delete` 删除、`Page Up/Page Down` 切换图片。

### 6.4 属性（可选）

1. 右侧点击 **公共**：设置所有类别共有的属性，例如“审核状态”。
2. 选中某类别后点击 **属性**：设置该类别独有的属性，例如“颜色”“损坏程度”。
3. 选中画布上或右侧具体标注行中的一个标注，右侧“标注属性”出现“公共属性”和“类别属性”。点击后可选择或更换属性值；单选值可反复更换。
4. 是/否属性提供独立的 **是**、**否** 和 **清除** 按钮；“否”会作为真实值保存，不等同于未填写。

属性定义保存在图片目录的 `class_attributes.json`；每个标注选择的实际属性值保存在相应 JSON 标注文件的 `attributes` 字段。

### 6.5 保存和导出

可在左侧选择 JSON、YOLO、XML 或 **CurveLanes / CondLaneNet 折线格式**，点击保存图标或按 `Ctrl + S`。

- **JSON（推荐长期保留）**：保留类别、全部几何形状和属性。
- **YOLO TXT**：只保留类别与坐标，不保留属性。
- **VOC XML**：只保留类别与坐标，不保留属性。
- **CurveLanes / CondLaneNet**：只导出开放折线为同名 `.lines.txt`，每行一条 `x1 y1 x2 y2 ...` 原图坐标；该训练格式不保存类别和属性。训练数据目录、清单生成和限制请阅读 `docs\CurveLanes-CondLaneNet导出与训练.md`。

默认保存到图片同目录。需要统一输出时，点击顶部 **保存目录** 并选择一个专用文件夹。标注期间不要频繁改变保存目录。

## 7. 必须备份的文件

重要项目至少备份以下内容：

- 原始图片；
- 与图片同名的 `.json` 标注文件；
- `class_attributes.json`。

建议每天或每完成一批图片后，复制整个图片目录到另一块硬盘或受控网盘。YOLO TXT / XML 无法找回属性，不能替代 JSON 备份。

## 8. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| 双击安装脚本后提示找不到 Python 或 `winget` | 从 Microsoft Store 安装/更新 **App Installer** 后重试；也可按第 3 节手动安装 Python。 |
| 双击启动脚本提示未安装 | 先双击 `Install-JotMark-Manual-Windows.bat`，等待出现 `Installation complete.`。 |
| 安装下载失败 | 检查网络、公司代理或安全软件；网络恢复后重新运行安装脚本即可。 |
| 报错中出现 `Windows Long Path support`、`No such file or directory`，且路径很长 | 使用新版安装包；它会把依赖安装到 `%LOCALAPPDATA%\JotMark\manual-venv`。若仍使用旧包，请将项目重新解压到 `E:\JotMark-Desktop` 等短路径后再安装。 |
| 想固定使用某个镜像 | 在 PowerShell 中先执行 `$env:JOTMARK_PIP_INDEX_URL='https://镜像地址/simple/'`，再运行安装命令；该设置只对当前 PowerShell 窗口有效。 |
| 程序一闪而过或打不开 | 使用双击启动脚本，不要直接双击 `main.py`；保留黑色窗口并截图其中报错给维护者。 |
| 提示 `DLL load failed` 或缺少运行库 | 在 Windows 设置中安装最新“Microsoft Visual C++ Redistributable x64”，然后重试。 |
| 看不到智能/SAM/模型/训练功能 | 正常现象；Windows 手工版故意不包含这些功能。 |
| 属性没有显示 | 确认打开的是原来的图片目录，其中应保留 `class_attributes.json` 和 JSON 标注文件。 |
| YOLO 或 XML 中没有属性 | 正常；这两种格式不支持任意属性，请同时保留 JSON。 |

## 9. 更新和卸载

更新前先备份图片与标注数据。将新版 ZIP 解压到新文件夹，重新运行安装脚本即可。不要直接覆盖旧目录的 `.venv-jotmark-manual`。

卸载时，在确认数据已备份后，直接删除 `JotMark-Desktop` 文件夹即可。标注数据若保存在其他目录，不会被删除。

## 10. 许可与来源

JotMark Desktop 基于 LabelPaw 的公开工作进行二次开发，遵循项目根目录 `LICENSE` 中的 GPL-3.0。上游致谢、第三方材料和责任说明见 `README.md` 与 `NOTICE.md`。Windows 手工版仅关闭且不安装 AI / 训练依赖，不改变项目许可证。
