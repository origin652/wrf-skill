# 后处理通用视图后续计划

状态日期：2026-04-09

这个文档记录 `wrf-post` 从当前 `schema_version=2` 运行时继续演进到更通用视图层的后续计划。
它不是协议草案本身，协议设计仍以 [post_view_protocol.zh-CN.md](/mnt/c/Users/dell/Documents/sepcific_skill/docs/post_view_protocol.zh-CN.md) 为准。

## 当前状态

当前已经具备这些基础能力：

- 内部 runtime 已经引入 `FieldCube -> ResolvedViewField` 的分层
- 地图视图、`time-x`、`time-height` 仍走统一的 view resolve 流程
- 第一版路径剖面已经可用
  - `x_axis.kind=path_coord`
  - `x_axis.name=distance_km`
  - `y_axis.name=bottom_top | height_m`
  - `sampling.path.kind=polyline`
- 真实 `wrfout` 已验证 `distance_km x height_m` 剖面可以生成

当前明确的边界：

- 路径采样还是最近网格点，不是双线性插值
- `height_m` 渲染仍是 first-pass，但已补显式 cell edges，真实数据 smoke 不再出现 `pcolormesh` 坐标 warning
- 还不支持 `pressure_hpa`
- 还不支持 `nearest_value`、`value`、`mean`、`min`、`max`、`sum`
- 还不支持截面矢量投影
- 还不支持把 `distance_km` 放到 `y_axis`

## 总体原则

- 保持 `layer -> view -> render` 三层分工，不再回退到“按图种堆条件分支”
- 尽量先扩内部运行时，再考虑是否升级外部协议版本
- 新增能力优先落在 `ViewResolver` 一侧，而不是塞进 renderer
- 每一阶段都要有 synthetic 测试和真实 `wrfout` smoke

## 阶段 1：把路径剖面做稳

目标：

- 把现有 `distance_km x bottom_top | height_m` 从可用提升到稳定

任务：

- 把最近网格点采样升级为双线性插值
- 为路径剖面补显式 cell edges 或更稳的网格构造，消除 `pcolormesh` warning
- 为 `height_m` 轴补更明确的 metadata 和单位
- 为真实数据增加固定 smoke case，避免后续回归

完成标准：

- 真实 `wrfout` 的路径剖面不再产生坐标 warning
- synthetic 和真实数据测试都稳定通过

## 阶段 2：补垂直派生坐标

目标：

- 支持 `pressure_hpa`
- 统一 `bottom_top`、`height_m`、`pressure_hpa` 三类垂直坐标的解析方式

任务：

- 在 runtime 中引入垂直坐标解析器
- 为 `PH/PHB`、`P/PB` 建立统一加载入口
- 让 `ResolvedViewField` 能携带 1D 或 2D 的垂直坐标
- 为 `pressure_hpa` 增加 synthetic 和真实数据测试

完成标准：

- `distance_km x pressure_hpa`
- `time x pressure_hpa`
- `time x height_m`

这三类图都能稳定生成。

## 阶段 3：补更完整的 selectors

目标：

- 让 view 不再只支持离散 index 选择

任务：

- 支持 `nearest_index`
- 支持 `value`
- 支持 `nearest_value`
- 支持 reduce 型 selector
  - `mean`
  - `min`
  - `max`
  - `sum`

完成标准：

- 用户可以按物理坐标选层
- 用户可以沿未显示维度做聚合，而不是只能固定一个 index

## 阶段 4：放开路径视图的轴组合

目标：

- 把路径剖面从“只能 `distance_km` 在 `x_axis`”放宽到更通用的组合

任务：

- 支持 `distance_km` 放在 `y_axis`
- 允许更多 view 轴组合校验
- 统一 axis metadata、label、units 的默认行为

完成标准：

- 相同数据层可以在不同轴组合下复用，不需要专门写图种逻辑

## 阶段 5：截面矢量协议

目标：

- 为非地图视图的矢量叠加建立显式协议

任务：

- 定义“轴空间投影”配置
- 支持把风场投影到路径切向/法向
- 明确哪些 view 允许矢量，哪些只允许标量

完成标准：

- 不再依赖“自动猜测矢量方向”
- 截面矢量的行为由协议显式描述

## 阶段 6：协议与文档收敛

目标：

- 把 runtime 的稳定能力沉淀回对外协议和示例

任务：

- 判断是否推进 `schema_version=3`
- 更新中英文协议文档
- 增加 path section、pressure section、selector reduce 的完整示例
- 更新 README 的“后处理协议”部分

## 建议执行顺序

建议优先级：

1. 路径剖面稳定化
2. `pressure_hpa`
3. 更完整的 selectors
4. 路径轴组合放开
5. 截面矢量协议
6. 文档和协议版本收敛

## 不建议提前做的事

- 三维体渲染
- 任意投影重投影
- 完整 GIS 坐标轴系统
- 所有视图上的自动矢量推断

## 代码落点

后续实现主要集中在这些文件：

- [plot_wrfout.py](/mnt/c/Users/dell/Documents/sepcific_skill/scripts/plot_wrfout.py)
- [post_spec.py](/mnt/c/Users/dell/Documents/sepcific_skill/scripts/post_spec.py)
- [test_wrf_post.py](/mnt/c/Users/dell/Documents/sepcific_skill/tests/test_wrf_post.py)
- [test_post_spec.py](/mnt/c/Users/dell/Documents/sepcific_skill/tests/test_post_spec.py)
