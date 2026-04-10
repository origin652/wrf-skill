# 通用后处理视图设计说明

状态：设计说明和后续扩展方向。这里不是当前可运行 `schema_version=3` 的精确机读合同。

这个仓库当前阶段 6 的结论是：当前已验证的 runtime 合同已经正式发布为 `schema_version=3`。

这份设计说明里的部分想法已经落进了当前发布的 v3 runtime，例如 `view_defs`、`time-x`、`time-y`、`time-height`、`time-pressure`、更丰富的 selectors、`distance_km` 路径剖面，以及显式的路径截面矢量投影。
这篇文档现在描述的仍然是超出当前已验证 v3 范围之后的设计思路，尤其是更通用的任意两轴组合和更广义的截面矢量行为。

当前可直接运行的行为见 `docs/post_runtime_v3.zh-CN.md`。

## 目标

这份草案最初的出发点，是更早期的后处理 runtime 基本会把每个 layer 都压成二维 `south_north x west_east` 场。
这足够支撑地图类产品，但会卡住这些需求：

- 时间-高度截面
- `west_east` 或 `south_north` 对时间的演变截面
- 任意路径的距离-高度截面
- 任何“不是水平地图面”的二维视图

下一版协议不应该继续加很多图种，比如 `map`、`time_height`、`distance_height`、`time_x`。
更好的方式是统一建模成三层：

1. 可复用的 n 维数据 layer
2. 可复用的二维视图定义
3. 现有的渲染 layer 叠加

## 核心思路

保留 `layer_defs` 负责算数据，但不要再要求每个 layer 在求值时立刻压成 2D。
新增 `view_defs`，专门描述“怎么从 n 维场里切出一个 2D 平面”。
然后 figure 只需要绑定一个 view。

建议的未来顶层结构：

```json
{
  "schema_version": 3,
  "layer_defs": {},
  "style_defs": {},
  "view_defs": {},
  "figures": []
}
```

## 建议的 `view_defs`

一个 view 只回答一个问题：

"哪两个轴组成最后的图面，其他轴怎么固定、聚合或采样？"

建议结构：

```json
{
  "view_id": "time_height_point",
  "x_axis": {},
  "y_axis": {},
  "selectors": {},
  "sampling": {}
}
```

### 轴定义

建议支持三类轴：

- 原生维度
  - `time`
  - `bottom_top`
  - `south_north`
  - `west_east`
- 派生坐标
  - `height_m`
  - `pressure_hpa`
  - `lat`
  - `lon`
- 采样坐标
  - 沿路径的 `distance_km`

建议结构：

```json
{
  "kind": "native_dim | derived_coord | path_coord",
  "name": "time | bottom_top | south_north | west_east | height_m | pressure_hpa | distance_km",
  "label": "可选，自定义坐标轴标题",
  "units": "可选，自定义坐标轴单位"
}
```

### Selectors

selectors 用来定义没有被选为 x/y 的那些维度应该怎么处理。

建议支持这些意图：

- 固定离散位置
  - `index`
  - `nearest_index`
- 固定物理坐标
  - `value`
  - `nearest_value`
- 运行时绑定
  - `current`
  - `first`
  - `last`
- 沿某个维度做聚合
  - `mean`
  - `min`
  - `max`
  - `sum`

建议结构：

```json
{
  "time": {
    "mode": "current"
  },
  "bottom_top": {
    "mode": "index",
    "index": 0
  },
  "south_north": {
    "mode": "nearest_index",
    "index": 42
  }
}
```

### Sampling

只有在视图平面不直接对齐原生网格时，才需要 sampling。

第一阶段最有价值的 sampling：

- 水平路径采样，用于 `distance_km x height_m`

建议结构：

```json
{
  "path": {
    "kind": "polyline",
    "points": [
      {"lat": 31.20, "lon": 121.40},
      {"lat": 31.80, "lon": 122.10}
    ],
    "samples": 200
  }
}
```

## Figure 绑定方式

每个 figure 绑定一个 view。
现有的 render layer 栈基本可以保留。

建议 figure 形态：

```json
{
  "figure_id": "theta_time_height",
  "view_id": "time_height_point",
  "layers": [
    {
      "layer_id": "theta",
      "style_id": "theta_raster"
    }
  ]
}
```

这样职责划分很清楚：

- `layer_defs` 决定“算什么数据”
- `view_defs` 决定“取哪个二维面”
- `style_defs` 决定“怎么画”
- `figures` 决定“哪些组合真的产出文件”

## 示例 views

### 1. 标准地图视图

```json
{
  "view_id": "map_xy_current",
  "x_axis": {"kind": "native_dim", "name": "west_east"},
  "y_axis": {"kind": "native_dim", "name": "south_north"},
  "selectors": {
    "time": {"mode": "current"}
  }
}
```

### 2. 单点时间-高度截面

```json
{
  "view_id": "time_height_point",
  "x_axis": {"kind": "native_dim", "name": "time"},
  "y_axis": {"kind": "derived_coord", "name": "height_m"},
  "selectors": {
    "south_north": {"mode": "nearest_index", "index": 30},
    "west_east": {"mode": "nearest_index", "index": 45}
  }
}
```

### 3. 任意路径距离-高度截面

```json
{
  "view_id": "distance_height_line",
  "x_axis": {"kind": "path_coord", "name": "distance_km"},
  "y_axis": {"kind": "derived_coord", "name": "height_m"},
  "selectors": {
    "time": {"mode": "current"}
  },
  "sampling": {
    "path": {
      "kind": "polyline",
      "points": [
        {"lat": 31.20, "lon": 121.40},
        {"lat": 31.80, "lon": 122.10}
      ],
      "samples": 200
    }
  }
}
```

### 4. 固定 y 和层的时间-x 视图

```json
{
  "view_id": "time_x_fixed_y_level",
  "x_axis": {"kind": "native_dim", "name": "time"},
  "y_axis": {"kind": "native_dim", "name": "west_east"},
  "selectors": {
    "south_north": {"mode": "nearest_index", "index": 50},
    "bottom_top": {"mode": "index", "index": 0}
  }
}
```

## 执行语义

figure 是逐时出图还是出一张范围图，不应该再由“地图模式”硬编码决定，而应该由 view 里剩余的运行时 selector 决定。

建议规则：

- 如果 `time` 本身就是绘图轴之一，那么对所选时间范围出一张图
- 如果 `time` 不是绘图轴，但最终 view 仍然依赖 `current(time)`，那么逐时出图
- 如果 `time` 已经被 `first/last/range/reduce` 之类方式解决，那么只出范围图

这和现在的 `current/first/last` 思路一致，只是它会被推广到通用 view 层。

## Layer 求值模型

runtime 需要从“只支持 2D field”升级成一个小型内部字段对象。

建议内部结构：

```text
FieldCube
- values: ndarray
- dims: ["time", "bottom_top", "south_north", "west_east"]
- coords: 可选，坐标数组或坐标解析器
- units
- metadata
```

`layer_defs` 负责生成 `FieldCube`。
`view_defs` 负责把它变成 2D 的 `ResolvedViewField`。
最终 renderer 仍然只负责二维渲染。

## 推荐实施顺序

1. 先在内部引入 n 维 field 元数据
2. 把已验证的 `view_defs` 和 `figure.view_id` 模型正式发布为 `schema_version=3`
3. 第一阶段只实现轴对齐视图：
   - 地图 `x/y`
   - `time/height`
   - `time/x`
   - `time/y`
4. 第二阶段补垂直派生坐标：
   - `height_m`
   - `pressure_hpa`
5. 第三阶段补路径采样，支持 `distance_km`
6. 标量截面稳定后，再回头看非地图 view 的矢量叠加

## 第一阶段不建议纳入的范围

- 三维体渲染
- 等值面
- 任意投影重投影
- 完整 GIS 级坐标轴引擎
- 所有 view 上的自动矢量投影

## 关于矢量的现实问题

矢量一旦脱离地图 view，就不再天然清晰。

比如：

- 地图视图里，`u/v` 本来就对应 x/y
- 距离-高度截面里，水平分量通常应该先投影到路径切向
- 时间-高度视图里，箭头叠加本身可能就不成立

所以通用截面的第一阶段应该先把标量 layer 做稳。
后续如果要支持截面矢量，应该显式引入“轴空间投影”协议，而不是偷偷自动猜。
