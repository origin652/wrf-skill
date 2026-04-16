# 当前 WRF 后处理 v3 指南

状态日期：2026-04-10

这篇文档描述的是这个仓库里当前可直接运行、以 figure 为核心的 `schema_version=3` 后处理行为。
现在 `schema_version=4` 在保留这套 figure 合同的同时，又新增了 `region_defs` 和 `charts` 原生统计图能力。
它和下面几份文件配套：

- `config/post_schema.json`：可机读协议
- `templates/post_spec.example.json`：更完整的起始示例
- `docs/post_view_protocol.zh-CN.md`：当前设计说明和超出已验证范围之后的后续扩展方向

这个仓库在阶段 6 的结论是：已验证的 figure runtime 合同正式发布为 `schema_version=3`，当前统计图扩展层则发布为 `schema_version=4`。

## 现在稳定可用的部分

当前 v3 runtime 已经稳定支持这些结构：

- 可复用的 `layer_defs`
- 可复用的 `style_defs`
- 可复用的 `view_defs`
- 负责把输入、selectors、view 和 render layers 绑在一起的 `figures[*]`

`layer_defs[*].source.kind` 当前支持：

- `wrf_native_2d`
- `wrf_native_3d`
- `wrf_native_3d_full`
- `wrf_diag`
- `wrf_native`，作为 `wrf_native_2d` 的兼容别名

输入模式当前支持：

- `project_artifacts`
- `explicit_paths`
- `glob`

绘制类型当前支持：

- `raster`
- `contour`
- `categorical_fill`
- `vector`

当前矢量边界：

- `draw.kind=vector` 现在支持地图视图
- 路径视图在 `draw.style.axis_projection.kind=path_section` 时也支持矢量
- 路径截面矢量使用 `u_layer_id` 和 `v_layer_id` 提供水平分量
- 当 `axis_projection` 用到 `vertical` 时，路径截面矢量额外使用 `vertical_layer_id`
- 路径截面的轴空间分量当前支持 `path_tangent`、`path_normal` 和 `vertical`
- 通过 `wrf_native_3d_full` 读取的原生 WRF `U`、`V`、`W`，会在做截面 view resolve 之前先去 stagger 到 mass grid
- 现在的矢量样式模式先支持 `style.mode=quiver`
- 带时间轴的截面和时间-垂直截面目前仍只支持标量

## 当前支持的视图形态

### 1. 地图视图

稳定地图视图就是水平网格面：

- `west_east x south_north`
- `south_north x west_east`

如果 figure 没有显式提供 `view` 或 `view_id`，默认就是水平地图视图。

### 2. 轴对齐时间截面

当前 runtime 已支持这类原生维度截面：

- `time x west_east`
- `west_east x time`
- `time x south_north`
- `south_north x time`

这类视图通常搭配 selectors，把剩余原生维度固定或聚合掉。

### 3. 时间-垂直截面

当前派生坐标截面的范围是：

- `time x height_m`
- `height_m x time`
- `time x pressure_hpa`
- `pressure_hpa x time`

当前边界：

- 派生坐标视图必须是一条 `time` 轴配一条派生垂直轴
- 派生垂直轴目前只能是 `height_m` 或 `pressure_hpa`

### 4. 路径剖面

当前第一阶段路径剖面支持：

- `distance_km x bottom_top`
- `bottom_top x distance_km`
- `distance_km x height_m`
- `height_m x distance_km`
- `distance_km x pressure_hpa`
- `pressure_hpa x distance_km`

路径采样目前要求的形态：

```json
{
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

当前边界：

- 两条绘图轴里必须且只能有一条是 `path_coord`，并且 `name=distance_km`
- 另一条绘图轴必须是 `bottom_top`、`height_m` 或 `pressure_hpa`
- 路径剖面目前不支持把 `time` 当成绘图轴
- 路径截面矢量必须显式写 `draw.style.axis_projection`

## 当前支持的 Selector 模式

`view.selectors` 目前在原生维度上支持这些模式：

- `index`
- `nearest_index`
- `value`
- `nearest_value`
- `first`
- `last`
- `current`
- `mean`
- `min`
- `max`
- `sum`

当前边界：

- `current` 只允许用于 `time`

常见用途：

- 固定某一行：`south_north: { "mode": "index", "index": 50 }`
- 按物理值选最近层：`bottom_top: { "mode": "nearest_value", "value": 850 }`
- 沿未显示维度做聚合：`south_north: { "mode": "mean" }`

## 输出语义

一个 figure 是出一张“范围图”还是逐时出图，取决于解析后的 view 和 layer 用法：

- 如果 `time` 本身是绘图轴之一，输出模式就是 `frame_range`
- 如果 `time` 不是绘图轴，但解析后的 view 里用了 `time.mode=current`，输出模式就是 `per_frame`
- 否则 runtime 会回退到 layer 依赖关系，只有当渲染层仍依赖 `current(...)` 时才逐时出图

## 最小使用流程

生成一个起始 spec：

```bash
python3 scripts/post_spec.py --project-name demo --output post_spec.json
```

从更完整的示例开始：

```bash
cp templates/post_spec.example.json post_spec.json
```

这份模板现在已经带了这些完整可运行例子：

- 一个带 `mean` reduce selector 的 `time-x` 截面
- 一个 `time-pressure` 柱状截面
- 一个 `distance_km x height_m` 标量路径剖面
- 一个基于原生 WRF `U`、`V`、`W` 的路径截面矢量叠加
- 一个区域平均气温折线图
- 一个按区域分组的末时次气温柱状图
- 一个按区域分组的逐时均值箱线图

规范化并校验：

```bash
python3 scripts/post_spec.py --input post_spec.json --output post_spec.json
```

查看最终解释出来的执行计划：

```bash
python3 scripts/post_spec.py --input post_spec.json --interpret
```

直接渲染某个已命名的 figure：

```bash
python3 scripts/plot_wrfout.py \
  --wrfout runs/demo/wrf/wrfout_d01_2024-07-20_00:00:00 \
  --figure-id surface_temperature \
  --post-spec post_spec.json \
  --out surface-temperature.png
```

按项目运行后处理：

```bash
python3 scripts/wrf_post.py --project-name demo --post-spec runs/demo/post_spec.json
```

## 统计图片段示例（`schema_version=4`）

区域平均时间序列：

```json
{
  "chart_id": "west_box_t2_time_mean",
  "chart_kind": "line",
  "x": {"mode": "time", "label": "valid_time"},
  "series": [
    {
      "series_id": "west_mean",
      "label": "West Box Mean T2",
      "layer_id": "t2_c",
      "region_id": "west_box",
      "reduce": {"mode": "mean"}
    }
  ]
}
```

按区域分组的末时次对比：

```json
{
  "chart_id": "grouped_t2_last_frame",
  "chart_kind": "bar",
  "x": {"mode": "group", "group_ids": ["west_box", "east_box"], "label": "region"},
  "series": [
    {
      "series_id": "group_mean",
      "label": "Group Mean T2",
      "layer_id": "t2_c",
      "reduce": {"mode": "mean"}
    }
  ]
}
```

按区域分组的时间分布箱线图：

```json
{
  "chart_id": "grouped_t2_time_distribution",
  "chart_kind": "boxplot",
  "x": {"mode": "group", "group_ids": ["west_box", "east_box"], "label": "region"},
  "series": [
    {
      "series_id": "group_distribution",
      "label": "Time Distribution of Group Mean T2",
      "layer_id": "t2_c",
      "reduce": {"mode": "mean"}
    }
  ]
}
```

## 视图片段示例

固定一条 `south_north` 线的 `time x west_east`：

```json
{
  "x_axis": {"name": "time"},
  "y_axis": {"name": "west_east"},
  "selectors": {
    "south_north": {"mode": "index", "index": 50}
  }
}
```

固定单列的 `time x pressure_hpa`：

```json
{
  "x_axis": {"name": "time"},
  "y_axis": {"kind": "derived_coord", "name": "pressure_hpa"},
  "selectors": {
    "south_north": {"mode": "index", "index": 50},
    "west_east": {"mode": "index", "index": 50}
  }
}
```

`distance_km x height_m` 路径剖面：

```json
{
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

路径截面矢量叠加：

```json
{
  "u_layer_id": "u_path",
  "v_layer_id": "v_path",
  "vertical_layer_id": "w_path",
  "draw": {
    "kind": "vector",
    "style": {
      "mode": "quiver",
      "axis_projection": {
        "kind": "path_section",
        "x_component": "path_tangent",
        "y_component": "vertical"
      }
    }
  }
}
```

## 仍然属于后续工作

这些目前还不在 v3 的稳定范围里：

- 超出上面校验范围的任意两轴组合
- 超出显式路径截面 axis_projection 之外的更通用截面矢量
- 把 `time` 作为路径剖面的绘图轴
- 超出 `sampling.path.kind=polyline` 的更通用采样模型

当前可运行合同现在使用 `schema_version=3`。超出上面已验证范围的后续设计见 `docs/post_view_protocol.zh-CN.md` 和 `docs/post_view_roadmap.zh-CN.md`。
