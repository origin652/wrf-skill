# 面向 Claude Code 和 Codex 的 WRF Skill

`wrf-skill` 是一层给 Claude Code 和 Codex 用的 WRF 工作流封装，前提是你已经有可用的 WRF/WPS 环境。
它不是 WRF/WPS 安装器，也不是编译器，更不是完整发行版。

[Back to English README](README.md)

## 这个仓库到底做什么

这个仓库提供的是一套可复用的 WRF 工作流，让 AI 能帮你完成这些事：

- 初始化项目
- 生成 `simulation_spec.json`、`namelist.wps`、`namelist.input`
- 跑资料准备、WPS 和 WRF
- 查看状态和日志
- 支持本地和可选的 HPC 工作流

这个仓库明确不做这些事：

- 替你编译 WRF 或 WPS
- 自动接入任意新资料源
- 自带完整 `WPS_GEOG`
- 自动识别没暴露出来的 HPC 规则
- 放开任意本地 shell 命令链

## 你自己还要准备什么

在使用这个仓库之前，至少先准备好这些：

- Linux 或 WSL
- Python 3.10+
- 已经编译好的 WRF 和 WPS
- 必要的 `WPS_GEOG` 数据和支持文件
- 你自己的资料源访问方式
- 如果要用 HPC：调度器访问方式、登录路径和站点自己的运行配置

## 推荐使用路径

### Claude Code

这个仓库已经自带了 `.claude/skills/`，所以 Claude Code 最直接。
最简单的方式就是直接打开仓库。

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill
```

如果你要跑 HPC，先从示例配置复制一份：

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

然后直接在 Claude Code 里打开这个仓库。

### Codex

现在对 Codex 的推荐路径已经是 skill-first。
先把 WRF skills 安装到 Codex，再用 `wrf-workspace-init` 在任意地方生成一个干净工作区。

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill
bash scripts/install_codex_skills.sh
```

如果 Codex 已经开着，安装后建议新开一个窗口或新会话，让它重新加载 skill 列表。

然后你可以直接对 Codex 说：

- `用 wrf-workspace-init 在 /path/to/my-wrf-workspace 创建工作区。`
- `用 wrf-workspace-init 在当前目录创建一个 WRF 工作区。`

如果你想直接跑脚本，也可以：

```bash
bash ~/.codex/skills/wrf-workspace-init/scripts/init_workspace.sh \
  --target-root /path/to/my-wrf-workspace
```

生成完以后，真正做 WRF 工作时，直接在 Codex 里打开那个新工作区路径。

## `wrf-workspace-init` 会生成什么

它生成的是一个可搬运、可用的最小工作区，只包含工作流层，不包含你的私有运行环境。

会包含：

- `.claude/skills/`
- `config/`
- `scripts/`
- `templates/`
- `third_party/wps-support/`
- `runs/.gitkeep`

故意不包含：

- 私有的 `config/wrf_env.json`
- 编译好的 WRF/WPS 目录
- 完整 `WPS_GEOG`
- 历史运行输出
- 私有 SSH / 调度器凭证

如果你要在生成后的工作区里跑 HPC：

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

然后把站点相关参数补齐。

## Bundle 分发方式

如果你要把这套工作流发给别人，仍然可以走 bundle：

```bash
python3 scripts/package_skill_bundle.py --output dist/wrf-skill-bundle.tar.gz
```

解压后装进另一个工作区：

```bash
tar -xzf dist/wrf-skill-bundle.tar.gz
cd wrf-skill-bundle
python3 scripts/install_skill_bundle.py --target /path/to/workspace
```

只有在你明确想覆盖目标目录里已有 bundle 文件时，才加 `--force`。

## 运行边界

### 本地 runtime

本地 runtime 定制是收紧的，不是放开的。
只有在你明确需要时才使用 `custom_safe`。

关键边界：

- 只允许结构化 argv 模板
- 不允许原始 shell 字符串
- 不允许 `bash -lc`、`sh -c`、管道、重定向、`&&`、`;`、`source`、`module load` 这类命令链

### HPC runtime

AI 只能使用你已经通过文件和命令暴露出来的 HPC 信息。
也就是说，它通常可以：

- 读取 `config/wrf_env.json`
- 查看项目状态和日志
- 使用当前会话里已经可用的调度器访问方式

它不能自动做到：

- 猜出隐藏的集群策略
- 看到实时资源，除非环境本来就提供了查询方式
- 替你安装缺失依赖

## 第一个可用流程

最小本地流程如下：

```bash
python3 scripts/wrf_init.py --project-name demo
python3 scripts/wrf_config.py \
  --project-name demo \
  --request-text "East China, GFS, 2024-07-20 00:00 to 2024-07-20 12:00, local" \
  --run-mode local
python3 scripts/wrf_task.py start --project-name demo --step wrf-data
python3 scripts/wrf_task.py start --project-name demo --step wrf-wps
python3 scripts/wrf_task.py start --project-name demo --step wrf-run
```

如果你这次只想先验证预处理链路，跑到 `wrf-wps` 就可以停。

## 后处理协议

`post_spec.json` 是后处理和诊断请求的建议格式。
规范形态是 `schema_version=2`，顶层包含 `defaults`、`style_defs`、`layer_defs` 和 `figures`。

稳定部分：

- `layer_defs`：可复用的数据图层定义，例如 `t2_c`、`wind10m`、`terrain`、`accum_precip`
- `style_defs`：可复用的渲染样式定义，例如 raster、contour、categorical_fill 的公共样式
- `figures[*].inputs`：输入文件解析方式
- `figures[*].selectors`：时间和 domain 选择
- `figures[*].render`：图级渲染默认值
- `figures[*].output`：输出位置和 sidecar 行为
- `figures[*].layers[*].style_id` 和 `figures[*].layers[*].draw`：复用样式以及单图层覆盖

当前 `layer_defs[*].source.kind` 支持这几类：

- `wrf_native_2d`：直接读取 WRF 原生二维变量
- `wrf_native_3d`：读取 WRF 原生三维变量，并通过 `source.level_selector` 选层
- `wrf_diag`：调用内置诊断量，例如 `wind_speed_10m`、`wind_dir_10m`、`total_precip`、`temp_c_2m`、`rh2`
- `wrf_native`：仍然接受，作为 `wrf_native_2d` 的兼容别名

生成一个起始 spec：

```bash
python3 scripts/post_spec.py --project-name demo --output post_spec.json
```

如果你想直接从一个更完整的 v2 示例开始，里面已经包含可复用 layer、逐时 figure 和范围图 figure，可以直接：

```bash
cp templates/post_spec.example.json post_spec.json
```

规范化并校验已有 spec：

```bash
python3 scripts/post_spec.py --input post_spec.json --output post_spec.json
```

如果你想先看这份 spec 最终会被解释成什么执行计划，可以直接：

```bash
python3 scripts/post_spec.py --input post_spec.json --interpret
```

如果你想直接从一个或多个 `wrfout` 文件渲染某个已定义的 figure，也可以：

```bash
python3 scripts/plot_wrfout.py \
  --wrfout runs/demo/wrf/wrfout_d01_2024-07-20_00:00:00 \
  --figure-id surface_temperature \
  --post-spec post_spec.json \
  --out surface-temperature.png
```

可机读的协议文件在 `config/post_schema.json`。

## 怎么理解这个仓库

最合适的理解方式是：

- Claude Code 的 WRF skill 工作区
- Codex 的 WRF skill 包加工作区初始化器
- 一套可分发的 WRF 工作流层

不要把它理解成真正的 WRF/WPS 安装包。

## 第三方文件和许可证

轻量 WPS 支持文件见 [THIRD_PARTY.md](THIRD_PARTY.md)。
项目自行编写的文件按 [Apache-2.0](LICENSE) 发布。
