# ApolloScape Lane 类别与 `boundary` 标注规范

## 目的与审计结论

本规范以本机 `Report` 目录中的 `JotMark_ApolloScape_Lane_标注类别与属性图文规范_20261029.docx` 为本项目的几何与属性口径参考，供 JotMark 项目直接建立类别、导入类别文件及配置属性使用。Word 原件不包含在代码仓库中；本文件是可随仓库分发的文字规范。下文的“全部使用闭合多边形”是 **JotMark 项目约定**，不应误认为 ApolloScape 原始标注格式的要求。

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

## 几何标注约定：全部使用闭合多边形

在本项目中，**`boundary`、35 个官方道路标记以及按需绘制的 `void`、`noise`、`ignore` 一律使用 Polygon 工具创建闭合多边形**。不要用开放折线、矩形或中心线代替。以原图坐标沿可见油漆或实体外缘落点；放大只用于精确定位，不能据放大画面臆造原图没有的细节。

| 类别范围 | 本项目标注类型 | 绘制范围 |
| --- | --- | --- |
| 连续分隔线、引导线、边界线、停车位线 | 闭合多边形 | 沿每根可见油漆带的两侧外缘形成窄带；不要只画中心线或覆盖路面。 |
| 双实线、双虚线 | 每根分别绘制闭合多边形 | 两根油漆带不得合并；中间路面不纳入标注。 |
| 虚线、间断标记 | 每个可见实段分别绘制闭合多边形 | 不跨越空隙，也不把多个片段连接成一整块。 |
| 停止线、导流线、斑马线 | 闭合多边形 | 沿实际可见油漆外轮廓绘制；不连接彼此分离的条带。 |
| 箭头、禁止停车、注意标记及其他面状标记 | 闭合多边形 | 勾画可见图案，排除阴影、反光和非标记区域；分离部分不要跨空白连接。 |
| `boundary` | 闭合窄带多边形 | 有可见路沿、实体宽度时贴实体外缘；无实体宽度的 `road_edge` 按下述固定窄带规则处理。 |
| `void`、`noise`、`ignore` | 默认不绘制；确需绘制时使用闭合多边形 | 未标注处默认就是背景；只圈定任务明确要求且可以界定的区域，不把漏标或小目标自动变成忽略区。 |

**无实体宽度的 `boundary`**：对于可以可靠定位、但没有可见油漆或路沿宽度的 `road_edge`，以可见中心边界两侧等距扩展，生成闭合窄带。带宽按原图短边计算：`6 × 原图短边 / 1080` px，并限制为 **3–12 px**；例如短边 1080 px 用 6 px，短边 1536 px 约用 8.5 px。这里是项目规定的标注带宽，**不是实测油漆宽度，也不是小目标是否标注的像素阈值**。无法可靠定位的边界不要推测走向；仅当确有可以圈定的不可判定区域时，按项目要求使用 `ignore` 并填写 `review=need_review`。

保存与交付以保留 Polygon 几何及属性的 **JSON** 为准。YOLO 分割 TXT 可保存多边形类别与坐标，但不保存任意属性；VOC XML 在当前 JotMark 导出器中只有多边形的外接矩形框，不保留原多边形。`CurveLanes / CondLaneNet` 的 `.lines.txt` 只导出开放折线，**不会自动导出本规范的多边形**；若后续训练必须使用该格式，应另外制定经审核的多边形转中心线流程，不能直接切换格式当作已完成转换。

## 官方类别映射与项目扩展

| JotMark 类别 | TrainId | 官方 ID | English description | 中文说明 | 本项目几何 |
| --- | ---: | ---: | --- | --- | --- |
| `boundary` | 项目类别 0 | — | boundary | 道路/车道边界项目扩展类 | 多边形 |
| `s_w_d` | 1 | 200 | single white dividing | 白色单实分隔线 | 多边形 |
| `s_y_d` | 2 | 204 | single yellow dividing | 黄色单实分隔线 | 多边形 |
| `ds_w_dn` | 3 | 213 | double solid white dividing | 白色双实分隔线 | 多边形（逐根） |
| `ds_y_dn` | 4 | 209 | double solid yellow dividing | 黄色双实分隔线 | 多边形（逐根） |
| `sb_w_do` | 5 | 206 | single broken white dividing | 白色单虚分隔线 | 多边形（逐段） |
| `sb_y_do` | 6 | 207 | single broken yellow dividing | 黄色单虚分隔线 | 多边形（逐段） |
| `b_w_g` | 7 | 201 | white guiding boundary | 白色引导边界线 | 多边形 |
| `b_y_g` | 8 | 203 | yellow guiding boundary | 黄色引导边界线 | 多边形 |
| `db_w_g` | 9 | 211 | double broken white guiding | 白色双虚引导线 | 多边形（逐根逐段） |
| `db_y_g` | 10 | 208 | double broken yellow guiding | 黄色双虚引导线 | 多边形（逐根逐段） |
| `db_w_s` | 11 | 216 | double broken white stopping | 白色双虚停止线 | 多边形（逐根逐段） |
| `s_w_s` | 12 | 217 | single white stopping | 白色单实停止线 | 多边形 |
| `ds_w_s` | 13 | 215 | double solid white stopping | 白色双实停止线 | 多边形（逐根） |
| `s_w_c` | 14 | 218 | single white chevron | 白色导流/人字纹线 | 多边形 |
| `s_y_c` | 15 | 219 | single yellow chevron | 黄色导流/人字纹线 | 多边形 |
| `s_w_p` | 16 | 210 | single white parking | 白色单实停车位线 | 多边形 |
| `s_n_p` | 17 | 232 | single unspecified-color parking | 未指定颜色单实停车位线 | 多边形 |
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
| `b_n_sr` | 31 | 205 | unspecified shrinking boundary | 未指定颜色收窄边界线 | 多边形 |
| `d_wy_za` | 32 | 212 | white-yellow attention marking | 白黄色注意/减速标线 | 多边形 |
| `r_wy_np` | 33 | 227 | white-yellow no-parking marking | 白黄色禁止停车标记 | 多边形 |
| `vom_wy_n` | 34 | 223 | white-yellow miscellaneous marking | 白黄色其他道路标线 | 多边形 |
| `om_n_n` | 35 | 250 | other unspecified marking | 其他未指定标线 | 多边形 |
| `void` | 0（JotMark 类别 36） | 0 | void | 背景/未标线区域 | 默认不绘制；按需多边形 |
| `noise` | 255（JotMark 类别 37） | 249 | noise | 明显噪声、伪影或错误标记 | 默认不绘制；按需多边形 |
| `ignore` | 255（JotMark 类别 38） | 255 | ignored | 遮挡、反光、磨损等无法可靠判定区域 | 默认不绘制；按需多边形 |

## `boundary` 使用边界

`boundary` 只用于任务需要、但无法准确归入官方 ApolloScape 细类的边界。若目标已经明确属于 `b_w_g`、`b_y_g` 或 `b_n_sr`，必须优先使用对应官方类别，不要重复标为 `boundary`。无论属于 `road_edge` 还是 `curb`，都画闭合窄带多边形，不画中心折线；无实体宽度的 `road_edge` 使用上文规定的带宽，不能因此覆盖整个车道。

推荐标注范围包括：

- 无漆线但可见的道路外缘、路缘石边缘；
- 隔离带、护栏或道路可行驶区域的边缘；
- 因遮挡、磨损或强反光而无法可靠判断官方线型/颜色，但业务仍要求保留的边界；
- 任务明确要求、且在原图中可以可靠定位的虚拟车道边界；无法定位时不得凭空推断。

## 属性预留

`examples/apolloscape_lane_class_attributes.template.json` 已包含以下属性定义。将其复制到图片目录并命名为 `class_attributes.json` 后，JotMark 会自动加载。

### 公共属性建议

| 属性名 | 类型 | 建议值 |
| --- | --- | --- |
| `visibility` | 多选 | `visible`、`partly_occluded`、`partly_truncated`、`uncertain` |
| `source` | 单选 | `painted`、`faded`、`repaired` |
| `review` | 单选 | `normal`、`need_review` |

`visibility` 用于记录目标的可见性状态，可同时勾选 `partly_occluded`（部分遮挡）和 `partly_truncated`（部分截断，即标记延伸至图像边界外）。`visible` 表示无明显遮挡或截断且可可靠判定；通常不应与前两项同时选择。无法可靠判断时选择 `uncertain`。

### `boundary` 专属属性建议

| 属性名 | 类型 | 建议值 |
| --- | --- | --- |
| `boundary_kind` | 单选 | `road_edge`（道路边缘）、`curb`（路沿） |

为与现有 Scalabel 数据严格对齐，当前模板只提供以上 3 个公共属性和 `boundary_kind`。如后续业务需要，可再为 `boundary` 增加侧别、连续性或可行驶区域关系等属性。

属性的实际值保存在 JSON 标注的 `attributes` 字段；YOLO / VOC XML 不保存任意属性。因此应始终保留 JSON 与 `class_attributes.json`。

## 审计注意事项

- 文档所述“左实右虚 / 左虚右实”是紧邻双线组内子线的相对关系，不是新增官方类别；不要建立 `left_solid_right_broken` 等额外类别。
- 本项目的几何验收须检查：每个已绘制实例都为闭合多边形；双线逐根、虚线逐可见实段绘制；不把两根油漆带或间隔路面并入一个多边形。
- 在 JotMark 的 YOLO TXT 中，`noise`、`ignore` 是项目类别编号 `37`、`38`，**不会自动变成评测忽略值 255**。训练前必须在下游转换中显式排除或映射这些区域，并核对最终训练标签；不可把项目类别号当作官方评估编号。
- 官方 `ignoreInEval` 仅表示官方评测可忽略，不代表该类不存在。是否绘制 TrainId 3、5、6、9、10、11、13、14、15、17、19、23、27–30、32、34 等类，应按你的训练目标决定。
- 原始 `_bin.png` 是 RGB 像素语义标签，不是二值图、YOLO TXT、实例框或折线坐标；若需与官方像素标签对齐，必须按官方 RGB 色表解码。
- 若使用 `boundary` 训练独立任务，请确保训练配置把它作为项目类别 0，而不是误映射到官方 TrainId 31 的 `b_n_sr`。`void` 的 JotMark 类别编号为 36，不再等于其官方 TrainId 0；`noise` 与 `ignore` 应映射到忽略值 255。
