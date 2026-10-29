# JotMark Desktop

JotMark 是一个本地图像标注与 YOLO 训练桌面工具。本仓库是从上游 LabelPaw 整理出的桌面端工作区：提供矩形、旋转框（OBB）、多边形、折线、点和关键点标注，并可使用 SAM2、SAM3 和 Ultralytics YOLO 辅助标注；内嵌的 TrainingPlatform 可训练、查看和导出 YOLO 模型。

> 模型权重、训练数据、训练结果和本地运行环境均不随仓库分发。请按本文部署后自行放置权重。

> 只需要人工标注、并且部署电脑没有显卡或编程环境时，请使用手工标注版：Linux/Ubuntu 请见[部署指南](docs/手工标注版部署指南.md)，Windows 10/11 请见 [Windows 部署指南](docs/Windows手工标注版部署指南.md)。Linux 版执行 `./Start-JotMark-Manual-Linux.sh` 会自动检测并配置缺失的 Python/虚拟环境；它启动时不会导入或加载 PyTorch、SAM、YOLO、模型权重和训练平台。

> 要为 Conditional Lane Detection / CondLaneNet 制作车道线训练数据，请使用顶部的 `CurveLanes / CondLaneNet 折线格式`；其 `.lines.txt` 导出和训练清单生成方式见 [CurveLanes-CondLaneNet 导出与训练说明](docs/CurveLanes-CondLaneNet导出与训练.md)。

> 要标注 ApolloScape Lane 的 36 个官方 TrainId 类别并扩展英文 `boundary` 类别，请使用 [ApolloScape Lane 类别与 Boundary 标注规范](docs/ApolloScape_Lane类别与Boundary标注规范.md) 及对应的 [可导入类别文件](examples/apolloscape_lane_classes_with_boundary.txt)。

## 功能与模型范围

| 模型 | 用途 | 可运行设备 | 权重发现规则 |
| --- | --- | --- | --- |
| SAM3 | 点提示、文本提示的智能分割 | **仅 CUDA GPU** | `weights/sam_weights/` 中文件名含 `sam3` 的 `.pt`，推荐 `sam3.pt` |
| SAM2 / SAM2.1 | 点提示智能分割 | CUDA 或 CPU | `weights/sam_weights/` 中其他 `.pt`；文件名必须含 `tiny`、`small`、`base_plus`/`base` 或 `large`，以便匹配配置 |
| Ultralytics YOLO | 检测、分割、姿态、OBB 的预测辅助标注 | CUDA 或 CPU | `weights/yolo*_weights/` 中的 `.pt` 或 `.onnx` |
| TrainingPlatform | YOLO 训练、指标查看、模型导入与导出 | 取决于所选 Python / PyTorch 环境 | 在界面中选择 YOLO 权重与数据集配置 |

SAM3 支持短文本概念提示和点提示；SAM2 仅在本程序中提供点提示。模型列表只会在应用启动时扫描一次；复制新权重后请重启应用。

## 目录说明

```text
JotMark-Desktop/
├── main.py                    # 标注桌面端入口
├── jotmark/                   # 画布、标注格式、模型客户端与运行时配置
├── ui/                        # 界面和图标
├── TrainingPlatform/          # 内嵌 YOLO 训练平台
├── weights/                   # 本地模型权重（被 Git 忽略）
├── scripts/
│   ├── install_jotmark.sh     # 创建环境并安装项目 / SAM 依赖
│   └── run_desktop.sh         # 启动桌面端
├── requirements-core.txt      # 通用桌面端与训练依赖
└── requirements-ai.txt        # AI 依赖说明
```

## 1. 系统要求

- Linux 桌面环境和 Bash；建议 Python 3.10 或更高版本。
- 使用 SAM2 时，官方环境建议 Linux、Python 3.10+、PyTorch 2.5.1+ 与匹配的 torchvision。
- 使用 SAM3 时必须有可被 PyTorch 识别的 NVIDIA CUDA GPU；本项目会拒绝在 CPU 上加载 SAM3。
- 安装官方 SAM 源码时需要 `git` 和网络；仅用 YOLO 时可跳过 SAM 源码安装。
- 显存、CUDA 驱动和 PyTorch CUDA wheel 必须相互兼容。先用 PyTorch 官方安装选择器确定适合本机的命令。

确认 GPU 与 Python：

```bash
nvidia-smi
python3 --version
```

## 2. 首次安装

以下步骤以仓库根目录为当前目录。默认安装器会先审核 NVIDIA 驱动、CUDA、PyTorch、GPU 计算能力和显存：通过后安装 CUDA 版 PyTorch、桌面依赖与 SAM 源码；若电脑不满足 SAM3 的硬件条件，会明确提示原因并自动安装无模型的手工标注版，而不是留下不可用的半成品环境。

```bash
cd /path/to/JotMark-Desktop

# 一键检查硬件、安装 CUDA PyTorch、桌面端与 SAM2/SAM3 源码。
# 默认使用 PyTorch cu118；可通过 JOTMARK_TORCH_INDEX_URL 覆盖。
./scripts/install_jotmark.sh
```

默认 SAM3 安装条件为：可用 NVIDIA CUDA 驱动（默认 cu118 建议驱动 `>= 520`）、PyTorch 可识别 CUDA、GPU 计算能力 `>= 7.0`、显存 `>= 8 GiB`。12 GiB 及以上更适合大图；8–12 GiB 会继续安装并启用 FP16 缓解显存压力，但复杂图片仍可能显存不足。未满足条件时，脚本会自动调用 `Install-JotMark-Manual-Linux.sh` 对应的手工版安装流程，并输出手工版启动命令。

如果使用自维护的 CUDA / PyTorch 组合，可覆盖默认 wheel 源：

```bash
JOTMARK_TORCH_INDEX_URL=https://download.pytorch.org/whl/cuXXX ./scripts/install_jotmark.sh
```

如明确不希望自动安装手工版，可设置：

```bash
JOTMARK_DISABLE_MANUAL_FALLBACK=1 ./scripts/install_jotmark.sh
```

脚本会将官方 SAM2/SAM3 源码克隆到 `.vendor/`，并以 editable 模式安装。若已在本机下载源码，可避免重复下载：

```bash
SAM2_SOURCE=/path/to/sam2 SAM3_SOURCE=/path/to/sam3 ./scripts/install_jotmark.sh
```

仅使用 YOLO，不安装 SAM：

```bash
INSTALL_SAM2=0 INSTALL_SAM3=0 ./scripts/install_jotmark.sh
```

安装后检查环境：

```bash
.venv-jotmark/bin/python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
.venv-jotmark/bin/python -c "import sam2, sam3; print('SAM source packages OK')"
```

第二条命令只有在安装了 SAM2 和 SAM3 后才应成功。

## 3. 部署模型权重

默认权重目录是项目根目录下的 `weights/`。如果权重存放在另一块磁盘，启动前设置 `JOTMARK_WEIGHTS_DIR` 即可，无需复制文件：

```bash
export JOTMARK_WEIGHTS_DIR=/data/jotmark-weights
./scripts/run_desktop.sh
```

目录结构示例：

```text
weights/
├── sam_weights/
│   ├── sam3.pt
│   ├── sam2.1_hiera_tiny.pt
│   ├── sam2.1_hiera_small.pt
│   ├── sam2.1_hiera_base_plus.pt
│   └── sam2.1_hiera_large.pt
├── yolo11_weights/
│   ├── yolo11n.pt
│   ├── yolo11n-seg.pt
│   ├── yolo11n-pose.pt
│   └── yolo11n-obb.pt
└── yolo26_weights/
    └── custom-seg.onnx
```

### SAM3

1. 从 Meta 官方 SAM3 项目按其许可、模型访问要求和下载说明取得 checkpoint。
2. 将 checkpoint 保存为 `weights/sam_weights/sam3.pt`。文件名只要包含 `sam3` 也能被识别，但推荐保持此名称。
3. 确认 `torch.cuda.is_available()` 输出为 `True`。
4. 启动程序，在智能标注模式的模型选择器中选择 **SAM 3**。

SAM3 当前在本项目中只支持 CUDA。若提示 CUDA 不可用、显存不足或模型加载失败，请关闭占用 GPU 的程序，核验 PyTorch / NVIDIA 驱动匹配关系，或切换到 SAM2 / YOLO 的 CPU 模式。

### SAM2 / SAM2.1

1. 从 Meta 官方 SAM2 项目下载与官方代码匹配的 checkpoint。
2. 将文件放入 `weights/sam_weights/`。
3. 使用可识别的文件名，例如 `sam2.1_hiera_small.pt`。程序依文件名中的 `tiny`、`small`、`base_plus`/`base`、`large` 选择 SAM2.1 配置。
4. 重启程序，在模型选择器中选择相应 SAM2 模型。

若模型文件名不包含上述关键字，界面可能仍显示该文件，但程序无法推断相应配置；请重命名为上述标准命名之一。

### YOLO `.pt` / `.onnx`

1. 在 `weights/` 下新建符合 `yolo*_weights` 规则的目录，例如 `weights/yolo11_weights/`。
2. 将 Ultralytics YOLO 的 `.pt` 或导出的 `.onnx` 模型放入该目录。
3. 重启程序，在模型选择器中选择对应 YOLO 分类与模型。

YOLO 模型任务应与标注目标一致：检测使用检测模型，分割使用 `-seg`，姿态使用 `-pose`，旋转框使用 `-obb`。自训练的 `best.pt` 也可按同样规则放入目录。

## 4. 启动与设备选择

```bash
cd /path/to/JotMark-Desktop
./scripts/run_desktop.sh
```

启动脚本默认优先使用 `.venv-jotmark`；若它不存在，会依次兼容项目内的 `.venv-labelpaw` 和本机已验证的 `/home/nex/Documents/JotMark/.venv-labelpaw`。它会自动选择 CUDA（若 PyTorch 可用）或 CPU。可通过 `ENV_DIR` 和设备变量覆盖：

```bash
# 明确使用已验证的兼容环境并启用 GPU
ENV_DIR=/home/nex/Documents/JotMark/.venv-labelpaw JOTMARK_DEVICE=cuda ./scripts/run_desktop.sh

# 自动选择，默认值
JOTMARK_DEVICE=auto ./scripts/run_desktop.sh

# 强制 CPU：适合 SAM2 / YOLO，SAM3 不可用
JOTMARK_DEVICE=cpu ./scripts/run_desktop.sh

# 期望使用 GPU；驱动或 PyTorch 不可用时程序会回退到 CPU
JOTMARK_DEVICE=cuda ./scripts/run_desktop.sh
```

提示：本项目虽会在 `JOTMARK_DEVICE=cuda` 无法使用时回退 CPU，但 SAM3 加载阶段仍会明确报错；这是预期行为。

## 5. 标注与训练基本流程

1. 启动应用并导入图片目录。
2. 在右侧维护类别，选择矩形、多边形、OBB、关键点等标注工具。
3. 需要辅助标注时进入智能模式，在模型选择器加载 SAM 或 YOLO。
4. 审核并手动调整预测结果后导出数据集。
5. 打开内嵌 **TrainingPlatform**，选择 Python 运行环境、数据集 YAML、模型及训练参数。
6. 训练完成后在模型管理页面查看 `best.pt` / `last.pt`、指标并导出模型。

YOLO 数据集的典型布局如下；数据集 YAML 中的 `train`、`val` 和类别配置必须改为你自己机器上的路径和类别，仓库内的示例 YAML 含有开发机路径，不能直接复用。

```text
dataset/
├── images/
│   ├── train/
│   └── val/
├── labels/
│   ├── train/
│   └── val/
└── data.yaml
```

## 6. 类别属性与子属性

除类别外，JotMark 还支持为每个类别定义独立的属性树，并为每个标注实例分别填写属性值。

1. 点击右侧类别区域的 **公共**，可配置适用于全部类别的公共属性；选中一个类别后点击 **属性**，可配置仅该类别可见的类别属性。
2. 在弹窗中点击 **+ 属性** 新建顶层属性；选中一个属性后点击 **+ 子属性** 可无限层级添加子属性。
3. 属性支持单选、多选、文本、数值和是/否；单选、多选的可选值每行填写一个。右侧会显示当前属性值按钮，点击后弹出包含全部值的可搜索列表；多选可在列表中勾选多个值后确认。
4. 选中画布中的一个标注后，右侧 **标注属性** 区会显示“公共属性”和“类别属性”按钮；点击任一按钮可弹出该范围内的完整属性列表。父属性尚未填写时，其子属性不会显示；未配置选项的单选/多选属性可直接输入值。
5. 在右侧 **标注属性** 区双击属性名称，可直接修改属性标签；该修改会应用到当前类别的全部标注，不会丢失已填写的属性值。
6. 右侧顶部的 **类别标签** 是可编辑下拉框：可选择已有类别，也可直接输入新标签。更换类别会清空旧类别的实例属性，并加载新类别的属性表单。
7. 右侧类别区的 **属性导入** 可选择任意 `class_attributes.json` 模板并直接写入当前图片目录。导入会校验 JSON；如果当前已有属性配置，会先自动备份到 `.jotmark_backups/attribute-schema-import-时间戳/`，再要求确认替换。导入只替换属性定义，不会自动删除既有 JSON 标注中的属性值。

类别属性定义保存在数据集目录下的 `class_attributes.json`；每个标注实例的属性值保存在 JSON 标注中 shape 的 `attributes` 字段。旧 JSON 没有该字段时会作为空属性加载，保持兼容。

原有自动加载机制仍保留：打开图片目录时，只要同目录已有 `class_attributes.json`，JotMark 会自动读取它。手动导入只是将所选模板保存为当前项目的该文件，方便首次建项目或替换模板。

YOLO 与 VOC XML 标准不支持任意属性字段，因此这两种格式只导出类别和几何信息；如需保留属性，请将 JSON 作为标注源文件。

### 安全转换类别与属性

右侧类别区的 **转换** 按钮（或右击类别后选择 **安全转换类别 / 属性**）提供三种只写入新目录的迁移：

1. **类别 → 类别**：例如 `car` 合并为 `vehicle`。
2. **属性 → 类别**：例如 `boundary` 的 `boundary_kind=curb`、`median` 分别变成 `boundary_curb`、`boundary_median`。
3. **类别 → 属性**：例如 `boundary_curb` 变回 `boundary`，并写入 `boundary_kind=curb`。

转换窗口会显示源目录、自动备份目录和新项目目录。开始后会先在源目录下创建 `.jotmark_backups/category-conversion-时间戳/` 的**完整快照**，再把转换后的完整项目写入一个尚不存在的新目录；源项目不会被改写。输出目录不能与源目录相同，也不能位于源目录内部。多选属性不能直接提升成单一类别，避免一个标注被错误拆成多个类别。

也可脱离主界面单独启动转换工具：`python3 scripts/open_category_conversion.py /你的/源项目目录`。

## 7. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `No module named sam2` 或 `sam3` | 安装 PyTorch 后重新执行 `./scripts/install_jotmark.sh`；离线环境使用 `SAM2_SOURCE` / `SAM3_SOURCE` 指向本地源码。 |
| SAM3 提示需要 CUDA | 这是项目限制。确认 `torch.cuda.is_available()` 为 `True`，否则改用 SAM2 或 YOLO。 |
| 模型没有出现在选择器 | 核对 `JOTMARK_WEIGHTS_DIR`、目录命名和扩展名；复制权重后重启应用。 |
| SAM2 无法加载配置 | 使用含 `tiny`、`small`、`base_plus` 或 `large` 的标准 checkpoint 文件名，并安装官方 SAM2 源码。 |
| `run_desktop.sh` 找不到环境 | 设置 `ENV_DIR=/实际/虚拟环境路径 ./scripts/run_desktop.sh`，或重新运行安装脚本。 |
| GitHub 拒绝 push 大文件 | 模型权重不应提交。执行 `git rm --cached <权重文件>`，确认 `.gitignore` 生效后重新提交；发布模型请使用外部存储或 Git LFS。 |

## 8. Git 与权重策略

`weights/`、`.venv-jotmark/`、`.vendor/`、训练输出和运行时缓存均已写入 `.gitignore`。提交代码前建议检查：

```bash
git status
git diff --cached --stat
```

确认暂存区没有 `.pt`、`.onnx`、训练数据或令牌后再 push。

## 上游致谢、二次开发与许可证

**JotMark Desktop 是基于 [LabelPaw](https://github.com/luohuabuxiema/LabelPaw) 的二次开发项目，不是从零独立开发，也不是 LabelPaw 官方版本。** 本仓库在其公开源代码基础上进行桌面端拆分、修改与扩展；感谢 LabelPaw 原始作者 / 维护者 [luohuabuxiema](https://github.com/luohuabuxiema) 及所有贡献者在智能标注和训练平台方面的工作。

上游 LabelPaw 采用 GPL-3.0；本项目作为衍生项目继续使用根目录 [`LICENSE`](LICENSE) 中的 GPL-3.0。GPL-3.0 允许在满足条件时商用，因此不能以 README 或额外条款把该衍生项目改为“禁止商用”。本项目自身按非商业开源项目维护：不提供付费版本、商业授权、商业支持或任何商业保证；这项项目定位不限制 GPL-3.0 下接收者依法享有的权利。

本项目与 LabelPaw 原作者、Meta、Ultralytics、PyTorch、Qt/PySide 及其关联方不存在赞助、合作、认证或背书关系。SAM、YOLO 和其他第三方组件/模型分别适用其自身许可证与访问条款；使用者须自行确认其数据、模型、输出和实际应用的合规性。

完整的上游归属、GPL 边界、非商业项目定位、第三方材料和责任说明见 [NOTICE.md](NOTICE.md)。`NOTICE.md` 是说明文件，不取代或修改 GPL-3.0，也不能通过声明免除任何法定责任。
