# JotMark 导出 CurveLanes / CondLaneNet 车道线数据

JotMark 已支持 Conditional Lane Detection 仓库中 `CurvelanesDataset` 使用的车道线标注格式。该格式用于**开放折线**，不是多边形。

## 1. 该训练代码实际读取什么

在 `/home/nex/Downloads/conditional-lane-detection` 中，`mmdet/datasets/culane_dataset.py` 会从 `train.txt` 或 `valid.txt` 逐行读取图片相对路径，然后把图片名末尾的 `.jpg` 替换为 `.lines.txt` 来读取标注。

例如清单有：

```text
images/frame_0001.jpg
```

则训练时要求同目录存在：

```text
images/frame_0001.lines.txt
```

`.lines.txt` 的每一行是一条车道线，按原图像素坐标写成：

```text
x1 y1 x2 y2 x3 y3 ...
```

示例：

```text
120 710 180 610 245 520 310 450
1090 710 1030 610 970 520 920 450
```

该数据加载器会把每一条线作为一个车道实例，并统一赋予内部类别 `1`；**文件中没有类别 ID、类别名称或属性字段**。

## 2. 在 JotMark 中标注与导出

1. 打开图片目录。为保证训练代码能找到标注，推荐保持“保存目录”为默认的“图片同目录”。
2. 在顶部“格式”菜单选择 **`CurveLanes / CondLaneNet 折线格式`**。
3. 选择左侧工具栏的“折线标注”。依次点击车道线的点，双击结束一条折线。
4. 点击保存。对于 `images/frame_0001.jpg`，JotMark 会生成 `images/frame_0001.lines.txt`。

只有折线会导出。矩形、多边形、旋转框、点、骨架、类别和实例属性不会被写入 `.lines.txt`，这是 CondLaneNet 原始格式的限制，并非 JotMark 丢失数据。若需保留类别、属性和全部标注类型，请同时保留 JSON 格式标注作为项目源数据。

导入已有 `.lines.txt` 时，由于源格式没有类别信息，JotMark 会把所有折线暂时显示为当前类别列表中的第一个类别；该显示标签不会写回 CurveLanes 文件。

## 3. 推荐的数据目录

Conditional Lane Detection 的 CurveLanes 配置分别把训练集和验证集目录作为 `data_root`。建议整理为：

```text
Curvelanes/
├── train/
│   ├── images/
│   │   ├── frame_0001.jpg
│   │   ├── frame_0001.lines.txt
│   │   └── ...
│   └── train.txt
└── valid/
    ├── images/
    │   ├── frame_1001.jpg
    │   ├── frame_1001.lines.txt
    │   └── ...
    └── valid.txt
```

其中 `train/train.txt` 内容应类似：

```text
images/frame_0001.jpg
images/frame_0002.jpg
```

验证清单同理，路径相对 `valid/`。

## 4. 自动生成训练/验证清单

仓库附带 `scripts/create_condlanenet_list.py`。它会递归找到同时拥有 `.lines.txt` 的图片，并生成相对路径清单；默认跳过没有标注或空标注的图片。

在 JotMark 项目根目录执行：

```bash
python scripts/create_condlanenet_list.py /数据集路径/Curvelanes/train --output /数据集路径/Curvelanes/train/train.txt
python scripts/create_condlanenet_list.py /数据集路径/Curvelanes/valid --output /数据集路径/Curvelanes/valid/valid.txt
```

如果确实需要把“没有可见车道线”的空标注图片也用于训练，可额外加入 `--include-empty`。请先确认你的训练任务和评估定义允许负样本。

## 5. 与当前 Conditional Lane Detection 仓库配合时的注意事项

- 该仓库的 `CurvelanesDataset` 是针对 CurveLanes 数据设计的，只接受原图尺寸为 **2560×1440、1570×660 或 1280×720** 的图片；其他尺寸在 `prepare_train_img()` 中会被跳过。
- 训练时会裁去图像上部：分别裁掉 `640`、`180` 或 `368` 像素。JotMark 导出的是**原图坐标**，这正是该加载器希望接收的坐标；训练加载器会自行应用相同偏移。
- 它按 `.jpg` 查找同名 `.lines.txt`。最稳妥的做法是把训练图片统一保存为 `.jpg`。若使用 PNG 等格式，请检查并按需要修改 Conditional Lane Detection 中的 `replace('.jpg', '.lines.txt')` 逻辑。
- 折线点的方向通常应保持一致。建议每条车道都按“远处 → 近处”或“近处 → 远处”统一标注，避免数据集中方向混杂。

## 6. 最小检查

导出后，确认图片与标注配对：

```bash
find /数据集路径/Curvelanes/train/images -name '*.jpg' | head
find /数据集路径/Curvelanes/train/images -name '*.lines.txt' | head
sed -n '1,5p' /数据集路径/Curvelanes/train/train.txt
sed -n '1,5p' /数据集路径/Curvelanes/train/images/frame_0001.lines.txt
```

每条 `.lines.txt` 非空行必须有偶数个数值，且至少有四个数（两个点）。
