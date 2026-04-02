# WRF AI Skill 设计方案（v2）

## Context

WRF（Weather Research and Forecasting）模型使用门槛高、配置复杂、流程长。目标是创建一套 Claude Code 自定义 skill，让用户通过自然语言完成 WRF 全流程操作（数据准备 → WPS 预处理 → WRF 运行 → 后处理分析），支持本地测试与后续 HPC 集群扩展。

## 适用范围与约束

- 运行平台只支持 Linux 或 WSL，不支持 Windows 原生环境直接运行 WRF/WPS 二进制。
- skill 负责流程编排、参数生成、日志汇总与错误诊断；真正的执行宿主是 Linux/WSL shell。
- 第一阶段只追求跑通最小闭环：`GFS -> WPS -> real.exe -> wrf.exe -> wrfout`。
- 第一版优先支持 local/WSL 单机运行；HPC 作为第二阶段能力接入。
- 第一版只做 GFS 驱动数据；ERA5/FNL 放到增强阶段。

## 设计原则

- 单一事实来源：每个项目以 `runs/<project>/project.json` 作为唯一状态源。
- 受约束生成：自然语言先落成结构化 `simulation_spec.json`，再生成 namelist，避免直接从自然语言写 namelist。
- 可恢复执行：每一步都支持 `dry-run`、重复执行、失败后从当前状态恢复。
- 全链路可追踪：每一步都要记录输入、输出、日志路径、错误码和时间戳。
- 先闭环后扩展：先把 local/WSL 的小案例跑通，再做 HPC、更多数据源和更多可视化。

## 目录结构

```text
sepcific_skill/
├── .claude/
│   └── skills/
│       ├── wrf-init/
│       │   └── SKILL.md                    # 项目初始化
│       ├── wrf-config/
│       │   └── SKILL.md                    # 结构化配置 + namelist 生成
│       ├── wrf-data/
│       │   └── SKILL.md                    # 驱动数据下载
│       ├── wrf-wps/
│       │   └── SKILL.md                    # WPS 预处理流程
│       ├── wrf-run/
│       │   └── SKILL.md                    # WRF 执行（local/hpc）
│       ├── wrf-post/
│       │   └── SKILL.md                    # 后处理与可视化
│       └── wrf/
│           └── SKILL.md                    # 全流程编排入口
│
├── scripts/
│   ├── project_state.py                    # project.json 读写、状态机、工件登记
│   ├── namelist_parser.py                  # namelist 读写与校验
│   ├── render_config.py                    # simulation_spec -> namelist.*
│   ├── download_gfs.py                     # GFS 数据下载
│   ├── check_env.sh                        # Linux/WSL 环境检查
│   ├── submit_hpc.py                       # HPC 作业脚本渲染与提交
│   ├── sync_hpc.sh                         # 项目同步到集群
│   ├── collect_hpc.sh                      # 从集群回收日志与产物
│   └── plot_wrfout.py                      # wrfout 可视化工具
│
├── templates/
│   ├── namelist.wps.template               # WPS 配置模板
│   ├── namelist.input.template             # WRF 配置模板
│   ├── slurm_wrf.sh.template               # SLURM 作业脚本模板
│   ├── pbs_wrf.sh.template                 # PBS 作业脚本模板
│   └── project.json.template               # 新项目状态模板
│
├── config/
│   ├── wrf_env.json                        # WRF/WPS/HPC 环境配置
│   ├── simulation_schema.json              # 结构化模拟请求 schema
│   ├── physics_schemes.json                # 物理方案参数库
│   └── domains_presets.json                # 常用区域预设
│
├── tests/
│   ├── test_project_state.py
│   ├── test_namelist_parser.py
│   └── fixtures/
│
├── runs/
│   └── <project-name>/
│       ├── project.json                    # 当前项目唯一状态源
│       ├── simulation_spec.json            # 结构化模拟配置
│       ├── data/
│       ├── wps/
│       ├── wrf/
│       ├── output/
│       └── logs/
│
├── CLAUDE.md
└── PLAN.md
```

## 项目状态与公共约定

### `runs/<project>/project.json`

每个 skill 都必须先读取、再更新 `project.json`。建议字段如下：

```json
{
  "project_name": "typhoon-gaemi",
  "platform": "wsl",
  "status": "configured",
  "current_step": "wrf-config",
  "paths": {
    "project_root": "runs/typhoon-gaemi",
    "data_dir": "runs/typhoon-gaemi/data",
    "wps_dir": "runs/typhoon-gaemi/wps",
    "wrf_dir": "runs/typhoon-gaemi/wrf",
    "output_dir": "runs/typhoon-gaemi/output",
    "log_dir": "runs/typhoon-gaemi/logs"
  },
  "artifacts": {
    "namelist_wps": null,
    "namelist_input": null,
    "met_em_files": [],
    "wrfinput_files": [],
    "wrfout_files": []
  },
  "data_source": {
    "type": "gfs",
    "start_time": "2024-07-20_00:00:00",
    "end_time": "2024-07-23_00:00:00",
    "interval_hours": 3
  },
  "execution": {
    "mode": "local",
    "dry_run": false,
    "job_id": null
  },
  "last_error": null,
  "updated_at": "2026-03-30T18:00:00+08:00"
}
```

### 状态机

建议状态值：

- `created`
- `env_checked`
- `configured`
- `data_ready`
- `wps_ready`
- `real_ready`
- `running`
- `completed`
- `failed`

### 日志与错误规范

- 每一步输出固定日志到 `runs/<project>/logs/<step>.log`。
- 若内部还分子步骤，则使用 `runs/<project>/logs/<step>-<substep>.log`。
- `project.json.last_error` 至少记录：`step`、`code`、`message`、`log_path`、`time`。
- skill 返回给用户时优先引用错误码与日志路径，不只返回自然语言失败描述。

### 通用执行选项

建议所有 skill 统一支持：

- `project=<name|path>`
- `dry-run=true|false`
- `resume=true|false`

`dry-run=true` 的含义：

- 只做参数校验、路径检查、命令渲染、脚本生成预览。
- 不执行 `geogrid.exe`、`ungrib.exe`、`metgrid.exe`、`real.exe`、`wrf.exe`、`sbatch/qsub` 等真实命令。

## Skill 详细设计

### 1. `/wrf` — 全流程入口（编排 skill）

- 用途：接收自然语言需求，转成结构化请求，并按状态机编排各子 skill。
- 参数：自然语言描述，例如 `"用 GFS 数据模拟 2024 年 7 月 22 日台风格美登陆，区域覆盖华东，分辨率 9km/3km，模拟 72 小时"`。
- 逻辑：
  1. 解析自然语言，生成 `simulation_spec.json`
  2. 用 `config/simulation_schema.json` 校验结构化参数
  3. 创建项目或读取已有项目状态
  4. 按顺序调用 `wrf-init -> wrf-config -> wrf-data -> wrf-wps -> wrf-run -> wrf-post`
  5. 任何一步失败时停止，并根据 `project.json.last_error` 报告下一步建议

### 2. `/wrf-init` — 项目初始化

- 用途：创建项目目录、初始化状态、检查 Linux/WSL WRF 环境。
- 参数：`project=<project-name>`，可选 `dry-run=true`
- 逻辑：
  1. 读取 `config/wrf_env.json`
  2. 运行 `scripts/check_env.sh` 检查 WRF/WPS、MPI、Python 环境
  3. 创建项目目录：`runs/<project-name>/{data,wps,wrf,output,logs}`
  4. 写入 `project.json` 和 `simulation_spec.json` 初始模板
  5. 复制 namelist 模板到项目目录
  6. 将状态更新为 `created` 或 `env_checked`

### 3. `/wrf-config` — 结构化配置与 Namelist 生成

- 用途：把自然语言需求转换为结构化模拟配置，再渲染 `namelist.wps` 和 `namelist.input`。
- 参数：自然语言配置或 JSON override，例如 `"双重嵌套，母域 27km 覆盖华东，子域 9km 覆盖浙江，PBL 用 YSU"`。
- 逻辑：
  1. 把用户请求落成 `simulation_spec.json`
  2. 根据 `config/domains_presets.json` 解析区域与默认分辨率
  3. 根据 `config/physics_schemes.json` 选择物理方案
  4. 用 `scripts/render_config.py` 生成目标配置
  5. 用 `scripts/namelist_parser.py` 写入 namelist 文件
  6. 验证时间范围、dx/dy、parent_grid_ratio、e_we/e_sn、time_step 等一致性
  7. 写回 `project.json`，状态设为 `configured`

### 4. `/wrf-data` — 数据下载

- 用途：下载驱动场数据并登记数据清单。
- MVP 范围：只支持 GFS。
- 参数：`source=gfs start=<time> end=<time>`，可选 `dry-run=true`
- 逻辑：
  1. 读取 `simulation_spec.json` 和 `project.json`
  2. 调用 `scripts/download_gfs.py`
  3. 验证文件数量、时间覆盖、文件命名和缺口
  4. 生成 `data_manifest.json`
  5. 更新 `project.json.artifacts` 和 `status=data_ready`

### 5. `/wrf-wps` — WPS 预处理

- 用途：执行 WPS 三步流程并验证中间产物。
- 参数：`project=<name|path>`，可选 `dry-run=true`
- 逻辑：
  1. 检查 `namelist.wps`、地理数据路径、Vtable 选择
  2. 运行 `geogrid.exe`，校验 `geo_em*`
  3. 运行 `link_grib.csh + ungrib.exe`，校验中间文件
  4. 运行 `metgrid.exe`，校验 `met_em*`
  5. 每一步写日志、检查退出码、更新 `project.json`
  6. 成功后状态设为 `wps_ready`

### 6. `/wrf-run` — WRF 执行

- 用途：运行 `real.exe` 和 `wrf.exe`，支持 local/WSL 与 HPC。
- 参数：`mode=local|hpc project=<name|path>`，可选 `dry-run=true`

- local/WSL 模式逻辑：
  1. 将 `met_em*` 链接或复制到 WRF 运行目录
  2. 运行 `real.exe`，校验 `wrfinput*` / `wrfbdy*`
  3. 状态更新为 `real_ready`
  4. 运行 `mpirun -np N wrf.exe`
  5. 监控 `rsl.out.*` / `rsl.error.*`
  6. 发现 `wrfout*` 后登记到 `project.json.artifacts`
  7. 成功后状态设为 `completed`

- HPC 模式逻辑：
  1. 用 `scripts/sync_hpc.sh` 将项目同步到远程运行目录
  2. 用 `scripts/submit_hpc.py` 根据 `wrf_env.json` 渲染 SLURM/PBS 脚本
  3. 自动写入模块加载、环境激活、MPI 启动命令
  4. 通过 `sbatch` 或 `qsub` 提交作业
  5. 将 `job_id`、远程路径、提交时间写回 `project.json`
  6. 提供状态查询命令模板，如 `squeue -j <job_id>` 或 `qstat <job_id>`
  7. 作业结束后运行 `scripts/collect_hpc.sh` 拉回日志、`wrfout` 元数据与产物
  8. 若失败，则记录远程日志路径、调度器状态、退出码和建议排查项

### 7. `/wrf-post` — 后处理分析

- 用途：读取 `wrfout`，提取变量并绘图。
- 参数：自然语言分析需求，例如 `"画出 24 小时累计降水分布图"`。
- 逻辑：
  1. 从 `project.json` 定位 `wrfout*`
  2. 用 `wrf-python` / `netCDF4` 读取变量
  3. 用 `scripts/plot_wrfout.py` 生成图件
  4. 结果保存到 `runs/<project>/output/plots/`

## 支撑脚本设计

### `project_state.py`

- `load_project(path)`：读取 `project.json`
- `save_project(state, path)`：写回 `project.json`
- `transition(state, next_status)`：状态机迁移检查
- `register_artifact(state, kind, path)`：登记工件
- `record_error(state, step, code, message, log_path)`：记录错误

### `namelist_parser.py`

- `read_namelist(path)` -> `dict`
- `write_namelist(config, path)`
- `validate_namelist(config)` -> `list[str]`
- `merge_namelist(base, overrides)` -> `dict`

### `render_config.py`

- 输入：`simulation_spec.json`
- 输出：`namelist.wps`、`namelist.input` 对应的结构化配置
- 职责：把自然语言经 schema 约束后的参数映射到 WRF/WPS 所需字段

### `download_gfs.py`

- 数据源：AWS S3 `noaa-gfs-bdp-pds` 或 NOMADS
- 功能：按时间范围、步长、分辨率下载 GRIB2
- 要求：支持断点续传、缺口检测、清单输出

### `check_env.sh`

- 只在 Linux/WSL 中执行
- 检查项：`geogrid.exe`、`ungrib.exe`、`metgrid.exe`、`real.exe`、`wrf.exe`、`mpirun`、Python 包依赖、地理数据路径

### `submit_hpc.py`

- 输入：`project.json` + `config/wrf_env.json`
- 输出：调度脚本路径、`job_id`
- 功能：按域大小推荐核数，渲染 SLURM/PBS 模板，支持提交与 dry-run

### `sync_hpc.sh` / `collect_hpc.sh`

- `sync_hpc.sh`：同步本地项目到远程工作目录
- `collect_hpc.sh`：拉回远程日志、关键输出和元数据
- 推荐优先使用 `rsync`，并明确排除大体积无关文件

### `plot_wrfout.py`

- 基于 `matplotlib + cartopy + wrf-python`
- 预定义图件：累计降水、2m 温度、10m 风场、500hPa 高度场、台风路径

## 关键配置文件

### `config/wrf_env.json`

```json
{
  "platform": "wsl",
  "shell": "bash",
  "wrf_dir": "/home/user/WRF",
  "wps_dir": "/home/user/WPS",
  "geog_data_path": "/data/WPS_GEOG",
  "run_mode": "local",
  "local": {
    "mpi_cmd": "mpirun",
    "default_np": 8
  },
  "hpc": {
    "enabled": true,
    "remote_host": "login.cluster.example",
    "remote_base_dir": "/scratch/user/wrf_runs",
    "scheduler": "slurm",
    "partition": "normal",
    "account": "myproject",
    "max_nodes": 4,
    "cores_per_node": 32,
    "modules": [
      "intel",
      "openmpi",
      "netcdf"
    ],
    "submit_cmd": "sbatch",
    "status_cmd": "squeue -j {job_id}",
    "cancel_cmd": "scancel {job_id}"
  },
  "python_env": "wrf-env"
}
```

### `config/simulation_schema.json`

必须约束的核心字段：

- `project_name`
- `data_source`
- `start_time`
- `end_time`
- `domains`
- `physics`
- `run_mode`

这样 `/wrf-config` 不会直接拿自然语言去拼 namelist，而是先变成可校验的结构化请求。

### `config/physics_schemes.json`

建议包含：

- 方案编号
- 名称与简称
- 适用场景
- 推荐搭配
- 不兼容组合说明

### `config/domains_presets.json`

建议包含：

- 区域名称
- 中心经纬度
- 默认范围
- 推荐分辨率
- 推荐嵌套层数

## HPC 流程补全

HPC 不能只停留在“生成并提交作业脚本”，至少要覆盖：

1. 本地项目目录同步到远程运行目录
2. 远程模块/环境初始化
3. 作业脚本渲染与提交
4. `job_id` 持久化到 `project.json`
5. 状态查询与失败诊断
6. 日志与关键产物回收
7. 提供取消命令与重试入口

若这一套没有定义完整，就不要把 HPC 写成“已支持”，最多写成“具备脚本生成与提交流程设计”。

## 实施顺序

### Phase 0：基础约定与最小骨架

1. 项目骨架：目录结构 + `CLAUDE.md` + `wrf_env.json`
2. `project.json` 模板 + `project_state.py`
3. 统一日志/错误格式
4. 所有 skill 的 `dry-run` / `resume` 公共约定
5. `check_env.sh`

验收条件：

- `/wrf-init project=test dry-run=true` 能输出完整执行计划
- `/wrf-init project=test` 能创建项目目录和 `project.json`
- `project.json` 字段与状态机约定稳定

### Phase 1：Local/WSL MVP 闭环

1. `simulation_schema.json`
2. `namelist_parser.py`
3. `render_config.py`
4. `/wrf-config` + namelist 模板 + `physics_schemes.json`
5. `download_gfs.py`
6. `/wrf-data`
7. `/wrf-wps`
8. `/wrf-run`（仅 local/WSL）

验收条件：

- 能从自然语言生成 `simulation_spec.json`
- 能生成通过校验的 `namelist.wps` 和 `namelist.input`
- 能用 GFS 数据完成小区域 6 小时模拟
- 能产出至少一个 `wrfout_d01*`

### Phase 2：HPC 接入

1. `submit_hpc.py`
2. `slurm_wrf.sh.template` / `pbs_wrf.sh.template`
3. `sync_hpc.sh` / `collect_hpc.sh`
4. `/wrf-run`（HPC 模式）

验收条件：

- `dry-run` 能渲染完整作业脚本与提交命令
- 能成功提交测试作业并回写 `job_id`
- 能查询状态并回收日志
- 失败时能在 `project.json.last_error` 中拿到调度器级别信息

### Phase 3：后处理与总编排

1. `plot_wrfout.py`
2. `/wrf-post`
3. `/wrf` 总编排 skill

验收条件：

- 能基于已有 `wrfout` 生成一张 PNG 图
- `/wrf` 能从自然语言请求跑到至少一个有效终点
- 失败时能明确告诉用户停在哪一步、下一步该做什么

### Phase 4：增强

1. `download_era5.py`
2. `download_fnl.py`
3. `domains_presets.json` 扩充
4. 更多可视化模板
5. 更多物理方案知识库

验收条件：

- 新数据源接入不破坏 GFS 主流程
- 新模板和预设均有最小验证样例

## 验证方式

1. 单元测试：`project_state.py`、`namelist_parser.py`、`render_config.py`
2. Skill 测试：`/wrf-init`、`/wrf-config`、`/wrf-data` 分别在空项目中验证状态迁移
3. 端到端测试：GFS 小域 6 小时模拟
4. HPC smoke test：脚本渲染、提交、查询、日志回收
5. 回归测试：重复执行同一项目，验证 `resume` 不会破坏已有工件

## 建议结论

这套方案可以做，但执行策略必须调整为：

- 平台限定为 Linux/WSL
- 第一版先做 local/WSL MVP 闭环
- 用 `project.json` 解决 skill 之间的状态传递
- 用 `simulation_spec.json` + schema 降低自然语言生成配置的风险
- HPC 明确补齐同步、提交、查询、回收、失败诊断

按这个版本推进，可行性明显高于原始方案，也更适合边做边验收。
