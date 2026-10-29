# ApolloScape Lane 类别与 `boundary` 标注规范

## 目的与审计结论

本规范依据 `ApolloScape_Lane标注类别图文说明_紧凑排版_void修正版.docx` 整理，供 JotMark 项目直接建立类别、导入类别文件及后续配置属性使用。

原始 ApolloScape Lane 的 **36 个训练类别**由以下两部分构成：

- `void`：TrainId `0`，背景/未标线区域；
- 35 个道路标记：TrainId `1`–`35`。

`noise`（官方 ID 249）与 `ignored`（官方 ID 255）均映射到官方评估忽略值 255。当前 JotMark 项目将其作为末尾辅助类别 `noise`、`ignore` 保留，便于在确有需要时表达噪声或不可判定区域；它们通常不应作为训练目标实例绘制。

本项目额外增加英文类别 `boundary`。它不是 ApolloScape 官方 TrainId 类别，而是用于人工补充道路/车道边界；它位于导入文件第 0 位。35 个官方道路标记仍位于类别编号 `1`–`35`，与其官方 TrainId 一致；`void` 位于 `36`，`noise` 位于 `37`，`ignore` 位于 `38`。

## 直接使用

1. 将 [apolloscape_lane_classes_with_boundary.txt](../examples/apolloscape_lane_classes_with_boundary.txt) 复制到图片目录并命名为 `classes.txt`，或在 JotMark 的类别区域点击 **导入** 选择该文件。
2. 如需预先创建属性文件，可将 [apolloscape_lane_class_attributes.template.json](../examples/apolloscape_lane_class_attributes.template.json) 复制到图片目录并命名为 `class_attributes.json`。
3. 类别顺序不要随意调整。YOLO 导出时类别编号依赖此顺序：`boundary=0`、35 种官方道路标记=`1..35`、`void=36`、`noise=37`、`ignore=38`。
4. 若旧项目此前使用 `boundary=36` 的旧类别顺序，**不要直接覆盖其 `classes.txt`**；已有 YOLO TXT 的编号会错位。应保留旧项目备份，并在新目录重新导出或使用类别/属性安全转换工具建立新项目。

## 几何标注约定

| 类别范围 | 建议几何 | 说明 |
| --- | --- | --- |
| 纵向分隔线、引导线、边界线 | 折线（Polyline） | 沿可见标线中心线或边界走向逐点标注；不要把折线闭合为多边形。 |
| 停止线、导流线、停车位线、斑马线 | 多边形或折线 | 需要面积语义时用多边形；只做中心线训练时用折线。 |
| 路面箭头、禁止停车、注意标记、其他面状标记 | 多边形 | 沿可见外轮廓勾画。 |
| `void`、`noise`、`ignore` | 默认不绘制 | 未标注区域天然表示背景；仅在任务明确要求时才绘制。`noise` 和 `ignore` 应在导出训练数据时映射为忽略值。 |
| `boundary` | 折线（首选） | 表示非官方细类难以覆盖的道路边缘、路缘、隔离带边缘或任务自定义边界。 |

## 官方类别映射与项目扩展

| JotMark 类别 | TrainId | 官方 ID | English description | 中文说明 | 建议几何 |
| --- | ---: | ---: | --- | --- | --- |
| `boundary` | 项目类别 0 | — | boundary | 道路/车道边界项目扩展类 | 折线 |
| `s_w_d` | 1 | 200 | single white dividing | 白色单实分隔线 | 折线 |
| `s_y_d` | 2 | 204 | single yellow dividing | 黄色单实分隔线 | 折线 |
| `ds_w_dn` | 3 | 213 | double solid white dividing | 白色双实分隔线 | 两条折线或多边形 |
| `ds_y_dn` | 4 | 209 | double solid yellow dividing | 黄色双实分隔线 | 两条折线或多边形 |
| `sb_w_do` | 5 | 206 | single broken white dividing | 白色单虚分隔线 | 折线 |
| `sb_y_do` | 6 | 207 | single broken yellow dividing | 黄色单虚分隔线 | 折线 |
| `b_w_g` | 7 | 201 | white guiding boundary | 白色引导边界线 | 折线 |
| `b_y_g` | 8 | 203 | yellow guiding boundary | 黄色引导边界线 | 折线 |
| `db_w_g` | 9 | 211 | double broken white guiding | 白色双虚引导线 | 两条折线或多边形 |
| `db_y_g` | 10 | 208 | double broken yellow guiding | 黄色双虚引导线 | 两条折线或多边形 |
| `db_w_s` | 11 | 216 | double broken white stopping | 白色双虚停止线 | 折线或多边形 |
| `s_w_s` | 12 | 217 | single white stopping | 白色单实停止线 | 折线或多边形 |
| `ds_w_s` | 13 | 215 | double solid white stopping | 白色双实停止线 | 折线或多边形 |
| `s_w_c` | 14 | 218 | single white chevron | 白色导流/人字纹线 | 多边形或折线 |
| `s_y_c` | 15 | 219 | single yellow chevron | 黄色导流/人字纹线 | 多边形或折线 |
| `s_w_p` | 16 | 210 | single white parking | 白色单实停车位线 | 折线 |
| `s_n_p` | 17 | 232 | single unspecified-color parking | 未指定颜色单实停车位线 | 折线 |
| `c_wy_z` | 18 | 214 | white-yellow zebra crossing | 白黄斑马线 | 多边形 |
| `a_w_u` | 19 | 202 | white U-turn arrow | 白色掉头箭头 | 多边形 |
| `a_w_t` | 20 | 220 | white straight-through arrow | 白色直行箭头 | 多边形 |
| `a_w_tl` | 21 | 221 | white straight-and-left arrow | 白色直行+左转箭头 | 多边形 |
| `a_w_tr` | 22 | 222 | white straight-and-right arrow | 白色直行+右转箭头 | 多边形 |
| `a_w_tlr` | 23 | 231 | white straight-left-right arrow | 白色直行+左转+右转箭头 | 多边形 |
| `a_w_l` | 24 | 224 | white left-turn arrow | 白色左转箭头 | 多边形 |
| `a_w_r` | 25 | 225 | white right-turn arrow | 白色右转箭头 | 多边形 |
| `a_w_lr` | 26 | 226 | white left-right turn arrow | 白色左转+右转箭头 | 多边形 |
| `a_n_lu` | 27 | 230 | unspecified left-and-U-turn arrow | 未指定颜色左转+掉头箭头 | 多边形 |
| `a_w_tu` | 28 | 228 | white straight-and-U-turn arrow | 白色直行+掉头箭头 | 多边形 |
| `a_w_m` | 29 | 229 | white merge arrow | 白色合流箭头 | 多边形 |
| `a_y_t` | 30 | 233 | yellow straight-through arrow | 黄色直行箭头 | 多边形 |
| `b_n_sr` | 31 | 205 | unspecified shrinking boundary | 未指定颜色收窄边界线 | 折线 |
| `d_wy_za` | 32 | 212 | white-yellow attention marking | 白黄色注意/减速标线 | 多边形 |
| `r_wy_np` | 33 | 227 | white-yellow no-parking marking | 白黄色禁止停车标记 | 多边形 |
| `vom_wy_n` | 34 | 223 | white-yellow miscellaneous marking | 白黄色其他道路标线 | 多边形 |
| `om_n_n` | 35 | 250 | other unspecified marking | 其他未指定标线 | 多边形 |
| `void` | 0（JotMark 类别 36） | 0 | void | 背景/未标线区域 | 默认不绘制 |
| `noise` | 255（JotMark 类别 37） | 249 | noise | 明显噪声、伪影或错误标记 | 默认不绘制 |
| `ignore` | 255（JotMark 类别 38） | 255 | ignored | 遮挡、反光、磨损等无法可靠判定区域 | 默认不绘制 |

## `boundary` 使用边界

`boundary` 只用于任务需要、但无法准确归入官方 ApolloScape 细类的边界。若目标已经明确属于 `b_w_g`、`b_y_g` 或 `b_n_sr`，必须优先使用对应官方类别，不要重复标为 `boundary`。

推荐标注范围包括：

- 无漆线但可见的道路外缘、路缘石边缘；
- 隔离带、护栏或道路可行驶区域的边缘；
- 因遮挡、磨损或强反光而无法可靠判断官方线型/颜色，但业务仍要求保留的边界；
- 项目定义的虚拟车道边界。

## 属性预留

`examples/apolloscape_lane_class_attributes.template.json` 已包含以下属性定义。将其复制到图片目录并命名为 `class_attributes.json` 后，JotMark 会自动加载。

### 公共属性建议

| 属性名 | 类型 | 建议值 |
| --- | --- | --- |
| `visibility` | 单选 | `visible`、`partly_occluded`、`uncertain` |
| `source` | 单选 | `painted`、`faded`、`repaired` |
| `review` | 单选 | `normal`、`need_review` |

### `boundary` 专属属性建议

| 属性名 | 类型 | 建议值 |
| --- | --- | --- |
| `boundary_kind` | 单选 | `road_edge`（道路边缘）、`curb`（路沿） |

为与现有 Scalabel 数据严格对齐，当前模板只提供以上 3 个公共属性和 `boundary_kind`。如后续业务需要，可再为 `boundary` 增加侧别、连续性或可行驶区域关系等属性。

属性的实际值保存在 JSON 标注的 `attributes` 字段；YOLO / VOC XML 不保存任意属性。因此应始终保留 JSON 与 `class_attributes.json`。

## 审计注意事项

- 文档所述“左实右虚 / 左虚右实”是紧邻双线组内子线的相对关系，不是新增官方类别；不要建立 `left_solid_right_broken` 等额外类别。
- 官方 `ignoreInEval` 仅表示官方评测可忽略，不代表该类不存在。是否绘制 TrainId 3、5、6、9、10、11、13、14、15、17、19、23、27–30、32、34 等类，应按你的训练目标决定。
- 原始 `_bin.png` 是 RGB 像素语义标签，不是二值图、YOLO TXT、实例框或折线坐标；若需与官方像素标签对齐，必须按官方 RGB 色表解码。
- 若使用 `boundary` 训练独立任务，请确保训练配置把它作为项目类别 0，而不是误映射到官方 TrainId 31 的 `b_n_sr`。`void` 的 JotMark 类别编号为 36，不再等于其官方 TrainId 0；`noise` 与 `ignore` 应映射到忽略值 255。
