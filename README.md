# JotMark Desktop

JotMark 是一个本地图像标注与 YOLO 训练桌面工具。本仓库是从上游 LabelPaw 整理出的桌面端工作区：提供矩形、旋转框（OBB）、多边形、折线、点和关键点标注，并可使用 SAM2、SAM3 和 Ultralytics YOLO 辅助标注；内嵌的 TrainingPlatform 可训练、查看和导出 YOLO 模型。

> 模型权重、训练数据、训练结果和本地运行环境均不随仓库分发。请按本文部署后自行放置权重。

## 功能与模型范围

| 模型 | 用途 | 可运行设备 | 权重发现规则 |
| --- | --- | --- | --- |
| SAM3 | 点提示、文本提示、选区合并的智能分割 | **仅 CUDA GPU** | `weights/sam_weights/` 中文件名含 `sam3` 的 `.pt`，推荐 `sam3.pt` |
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

以下步骤以仓库根目录为当前目录。创建虚拟环境后，**先安装与本机 CUDA 匹配的 PyTorch 和 torchvision**；不要把 CPU 版 PyTorch 当作 SAM3 环境。

```bash
cd /path/to/JotMark-Desktop

# 创建虚拟环境；脚本会提示你先完成 PyTorch 安装
./scripts/install_jotmark.sh

# 按 PyTorch 官方选择器给出的命令安装 CUDA 版 torch / torchvision，示例：
.venv-jotmark/bin/python -m pip install torch torchvision --index-url <PYTORCH_CUDA_INDEX_URL>

# 安装桌面端、YOLO、SAM2 和 SAM3 源码
./scripts/install_jotmark.sh
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

启动脚本默认优先使用 `.venv-jotmark`，并会自动选择 CUDA（若 PyTorch 可用）或 CPU。可覆盖设备策略：

```bash
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

## 6. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `No module named sam2` 或 `sam3` | 安装 PyTorch 后重新执行 `./scripts/install_jotmark.sh`；离线环境使用 `SAM2_SOURCE` / `SAM3_SOURCE` 指向本地源码。 |
| SAM3 提示需要 CUDA | 这是项目限制。确认 `torch.cuda.is_available()` 为 `True`，否则改用 SAM2 或 YOLO。 |
| 模型没有出现在选择器 | 核对 `JOTMARK_WEIGHTS_DIR`、目录命名和扩展名；复制权重后重启应用。 |
| SAM2 无法加载配置 | 使用含 `tiny`、`small`、`base_plus` 或 `large` 的标准 checkpoint 文件名，并安装官方 SAM2 源码。 |
| `run_desktop.sh` 找不到环境 | 设置 `ENV_DIR=/实际/虚拟环境路径 ./scripts/run_desktop.sh`，或重新运行安装脚本。 |
| GitHub 拒绝 push 大文件 | 模型权重不应提交。执行 `git rm --cached <权重文件>`，确认 `.gitignore` 生效后重新提交；发布模型请使用外部存储或 Git LFS。 |

## 7. Git 与权重策略

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
