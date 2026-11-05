# JotMark macOS 手工标注版部署指南

本指南适用于没有编程经验、使用 macOS 11 或更新版本的用户。完成后可使用矩形、多边形、折线、关键点和旋转框进行纯手工标注；不需要 NVIDIA 显卡、CUDA、SAM、YOLO、模型权重或训练平台。

手工版刻意不安装 PyTorch、SAM2、SAM3、YOLO、CUDA、模型权重和 TrainingPlatform。普通 Intel Mac 与 Apple Silicon Mac 均可使用；首次安装需要联网，之后日常手工标注不需要网络。

## 1. 解压与放置项目

1. 将三个系统通用的 `JotMark-手工标注版-20261103-v3.zip` 下载到“下载”目录（后续版本的日期、版本号可能不同）。
2. 双击 ZIP 解压，得到 `JotMark-Desktop` 文件夹。
3. 建议把整个文件夹移动到“文稿”或用户主目录，例如：

```text
/Users/你的用户名/Documents/JotMark-Desktop
```

不要放在 U 盘、iCloud Drive、Dropbox 或 OneDrive 的实时同步目录中。项目路径尽量不要过长，也尽量避免频繁改名或移动；图片标注数据应单独放到其他项目文件夹中。

解压后的顶层应至少包含：

```text
Install-JotMark-Manual-macOS.command
Start-JotMark-Manual-macOS.command
scripts/install_manual_annotation_macos.sh
scripts/run_manual_annotation_macos.sh
```

## 2. 一键检查、安装与启动

直接双击：

```text
Start-JotMark-Manual-macOS.command
```

它会自动检查以下内容：

- Python 是否为 3.10 或更高版本；
- JotMark 专用虚拟环境是否存在且可用；
- PySide6、OpenCV、NumPy、Pillow 是否完整；
- 手工模式是否能正常导入程序。

环境完整时会直接启动 JotMark。首次运行、环境缺失或依赖损坏时，启动器会自动调用安装流程。

也可以先双击以下文件单独完成安装：

```text
Install-JotMark-Manual-macOS.command
```

安装器会将依赖安装到当前用户目录，不会把庞大的第三方包写入项目文件夹：

```text
~/Library/Application Support/JotMark/manual-venv
```

## 3. 新 Mac 没有 Python 时会发生什么

安装器会先寻找已有的 Python 3.10+。若没有，会尝试使用 Homebrew 安装 Python 3.12；如果 Homebrew 不存在，会先在终端询问是否安装。Homebrew 被跳过、安装失败或无法提供 Python 时，自动测速清华/阿里云的 Python 3.12.10 macOS 安装包与 Python 官方来源，选取响应最快且文件大小正常的来源；下载不完整或签名校验失败会切换 Python 官方来源。只有验证安装包签名属于 Python Software Foundation，才会请求管理员密码并安装 `.pkg`。这两条途径都不可用时才需要手动配置 Python。

这个过程中可能要求输入 macOS 的登录密码。输入密码时终端不会显示字符，这是 macOS 的正常安全行为；输入完成后按 Enter 即可。

安装依赖前，脚本会短时测速清华 TUNA、中科大、阿里云和官方 PyPI，并使用本次响应最快的可用源；若所选镜像下载失败，会自动用官方 PyPI 重试一次。不会修改系统全局 pip 配置。

如果不希望自动安装 Homebrew，可在提示时输入 `n`，安装器会转用已验证签名的 `.pkg` 路径；系统安装 `.pkg` 仍需要管理员密码。无法获取管理员权限时，可自行配置 Python 3.10+ 后再运行启动器。

## 4. macOS 提示“无法打开”或“来自身份不明的开发者”

这是 macOS Gatekeeper 对从网络下载脚本的常见提示。确认压缩包来自可信发送者后：

1. 在 Finder 中按住 **Control** 键点击 `Start-JotMark-Manual-macOS.command`；
2. 选择 **打开**；
3. 在确认窗口中再次选择 **打开**。

若仍被拦截，可进入“系统设置 → 隐私与安全性”，在页面底部允许打开该文件，然后再次双击。

极少数解压工具会丢失脚本的可执行权限。此时打开“终端”，进入项目目录并执行：

```bash
cd ~/Documents/JotMark-Desktop
chmod +x Install-JotMark-Manual-macOS.command Start-JotMark-Manual-macOS.command
chmod +x scripts/install_manual_annotation_macos.sh scripts/run_manual_annotation_macos.sh
./Start-JotMark-Manual-macOS.command
```

仅当确认项目来自可信来源且 Gatekeeper 持续拦截时，才可在项目目录执行以下命令移除下载隔离标记：

```bash
xattr -dr com.apple.quarantine .
```

## 5. 每次启动

左上角侧边栏折叠按钮右侧的 **⚙ 设置** 可调整标注线宽（0.5–12 px，每档 0.5 px）、文件列表排序/后缀、主题及保存目录。线宽仅改变画布显示，不修改标注坐标；连续绘制沿用上次类别，右侧主动选择的类别优先。

画布标签第一行显示类别，其后 `◇` 表示公共属性、`◆` 表示类别属性；窄标注旁的文字会换行，属性很多时显示前 4 项和剩余数量，鼠标悬停标签可看完整已填写属性。在选择/编辑模式下，点击属性可直接修改，点击“另有…项”可查看完整属性列表，点击类别仍可修改类别。未填写的属性可在右侧设置；修改会同步至右侧并支持撤销/重做。请使用 JSON 保存属性，YOLO/XML 不保存属性。⚙ 设置可关闭“在画布标签中显示已填写属性”，但类别仍显示。

保存失败时，底部会显示“保存失败：未保存”，当前画布不会因切换或关闭而被丢弃；可点击 **另存恢复** 另存新的 JSON。标注上一版位于标注目录 `.jotmark_backups/annotation-history/文件名.previous`，删除空标注时旧文件移至 `.jotmark_trash/annotations/`；类别或属性批量变更前另有 `category-change-*` 快照。请定期把整个项目备份到其他磁盘：自动保存有延迟，强制结束或断电仍可能丢失最后的操作。不要让多个程序同时编辑同一项目；同名不同后缀图片会被阻止共用标注文件。详见 README 的“标注数据保护与恢复”。

画布中 **Ctrl + 滚轮** 会保持鼠标指向的图片位置不动进行缩放；放大后可用滚动条查看边缘。⚙ 设置中的 **图片边缘留白/投点距离** 默认为 40 px，可在 0–160 px 调整：图片适应窗口时四周预留空白；放大后滚动到边缘留白时，也能在可见画布内设定距离内点击多边形或折线，点会落在对应图片边缘。超过距离、点击位置不在画布中均不会新增点；设为 0 可关闭。

以后只需双击：

```text
Start-JotMark-Manual-macOS.command
```

启动器会每次进行轻量检查。若虚拟环境被误删、依赖安装不完整或 Python 不可用，它会自动转入安装/修复流程；无需手动输入 `pip install` 命令。

启动后看不到“智能”“SAM”“模型”“训练”等按钮是正常的：这说明当前运行的是无模型的纯手工标注版。

## 6. 第一次标注

1. 将待标图片放进独立文件夹，例如 `~/Documents/AnnotationProjects/ProjectA/images`。
2. 在 JotMark 中点击 **打开目录**，选择该图片文件夹。
3. 在右侧类别区域添加类别；新项目没有 `classes.txt` 时会加载 `person`、`car`、`cyclist` 示例类别。
4. 选择左侧标注工具，使用矩形、多边形、折线、关键点或旋转框绘制。
5. 按 `Command + S` 保存。JSON 可完整保留形状与属性；YOLO TXT、VOC XML 不保存任意属性。

如需属性，在右侧点击 **公共** 配置所有类别共用属性，或选择类别后点击 **属性** 配置类别专属属性。已有模板可使用 **属性导入** 导入 `class_attributes.json`；导入前程序会自动备份旧定义。

重要项目应定期备份原图、同名 JSON 标注、`classes.txt` 和 `class_attributes.json`。仅保留 YOLO 或 XML 无法恢复属性。

## 7. 常见问题

| 现象 | 处理方法 |
| --- | --- |
| 双击启动器后没有反应 | 在 Finder 中按住 Control 点击文件并选择“打开”；必要时按第 4 节用终端执行。 |
| 提示无法安装 Homebrew 或 Python | Homebrew 失败后会自动尝试签名 `.pkg`；检查网络、macOS 是否为 11+ 及管理员权限。 |
| 安装依赖时下载失败 | 检查网络、代理或安全软件；重新双击启动器即可恢复安装。 |
| 启动器重复安装 | 删除 `~/Library/Application Support/JotMark/manual-venv` 后重新运行启动器，或保留终端报错截图以便排查。 |
| 程序打开后立即关闭 | 从终端运行 `./Start-JotMark-Manual-macOS.command`，保留终端最后的报错信息。 |
| 看不到 SAM、模型或训练功能 | 正常；这是纯手工版，故意不包含上述功能。 |
| 属性没有显示 | 确认打开的是原来的图片目录，其中仍保留 `class_attributes.json` 与 JSON 标注文件。 |

## 8. 更新与卸载

更新前先备份图片和标注数据。将新版压缩包解压到新文件夹，然后双击新的启动器即可；它会复用并检查当前用户的手工标注环境。

若要彻底卸载，在确认标注数据已备份后删除项目文件夹，并在 Finder 中前往以下位置后删除 `manual-venv`：

```text
~/Library/Application Support/JotMark/
```

## 9. 许可与来源

JotMark Desktop 基于 LabelPaw 的公开工作进行二次开发，遵循项目根目录 `LICENSE` 中的 GPL-3.0。上游致谢、第三方材料和责任说明见 `README.md` 与 `NOTICE.md`。macOS 手工版仅关闭且不安装 AI / 训练依赖，不改变项目许可证。
