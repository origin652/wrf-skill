# WRF Skill

让 AI 助手帮你运行 WRF 模拟的工作流工具。

[English](README.md) | 简体中文

---

## 这是什么？

WRF Skill 是一套让 Claude Code 和 Codex 能够帮你操作 WRF 模型的工作流工具。如果你已经有编译好的 WRF/WPS 环境，这个工具可以让 AI 助手帮你：

- 🚀 快速初始化模拟项目
- ⚙️ 自动生成配置文件（namelist.wps、namelist.input）
- 📦 下载和准备气象资料（支持 GFS、FNL、ERA5）
- 🔄 运行完整的 WPS → WRF 工作流
- 📊 后处理和可视化输出结果
- 🖥️ 支持本地运行和 HPC 集群提交

**这不是什么：** 这不是 WRF 安装器或编译工具，你需要自己准备好 WRF/WPS 运行环境。

---

## 安装指南

### 系统要求

#### 必需环境
- **操作系统**: Linux 或 WSL2（Windows Subsystem for Linux 2）
- **Python**: 3.10 或更高版本
- **WRF/WPS**: 已编译并可运行的版本（推荐 WRF 4.x）
- **地理数据**: WPS_GEOG 完整数据集
- **存储空间**: 至少 50GB 可用空间（用于模拟输出）

#### Python 依赖
- netCDF4 >= 1.6.0
- numpy >= 1.24.0
- matplotlib >= 3.7.0
- cartopy >= 0.22.0
- xarray >= 2023.1.0

### 第一步：安装 WRF Skill

```bash
# 1. 克隆仓库
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill

# 2. 安装核心依赖
pip install -e .

# 3. （可选）如果你要参与开发或运行测试
pip install -e ".[dev]"

# 4. 验证安装
python3 scripts/wrf.py --version
# 应该输出: wrf-skill v0.1.0
```

### 第二步：配置运行环境

#### 本地运行配置

创建 `config/wrf_env.json` 文件：

```bash
# 复制模板（如果有）或手动创建
cat > config/wrf_env.json << 'EOF'
{
  "wrf_root": "/home/username/WRF",
  "wps_root": "/home/username/WPS",
  "geog_data_path": "/data/WPS_GEOG",
  "runtime": {
    "mode": "local",
    "wrf_nproc": 4
  }
}
EOF
```

**配置说明：**
- `wrf_root`: WRF 安装目录（包含 `main/wrf.exe` 的目录）
- `wps_root`: WPS 安装目录（包含 `geogrid.exe` 等的目录）
- `geog_data_path`: WPS_GEOG 地理数据路径
- `wrf_nproc`: 本地运行使用的 CPU 核心数

#### HPC 集群配置

如果你要在 HPC 集群上运行：

```bash
# 1. 从示例配置开始
cp config/wrf_env.hpc.example.json config/wrf_env.json

# 2. 编辑配置文件
nano config/wrf_env.json
```

**HPC 配置示例（Slurm）：**

```json
{
  "wrf_root": "/home/username/WRF",
  "wps_root": "/home/username/WPS",
  "geog_data_path": "/data/WPS_GEOG",
  "hpc": {
    "backend": "slurm",
    "remote_host": "login.hpc.university.edu",
    "remote_project_root": "/scratch/username/wrf-projects",
    "scheduler_ssh_cmd": ["ssh", "-i", "~/.ssh/id_rsa"],
    "runtime": {
      "mode": "mpirun",
      "wrf_nproc": 48,
      "partition": "compute",
      "walltime": "06:00:00",
      "account": "your_account",
      "modules": ["intel/2021.4", "openmpi/4.1.1", "netcdf/4.8.1"]
    }
  }
}
```

**HPC 配置说明：**
- `backend`: 调度器类型（`slurm` 或 `pbs`）
- `remote_host`: HPC 登录节点地址
- `remote_project_root`: 集群上的项目根目录
- `scheduler_ssh_cmd`: SSH 连接命令（可选，默认为 `ssh`）
- `wrf_nproc`: 使用的 MPI 进程数
- `partition`: 作业队列/分区名称
- `walltime`: 最大运行时间
- `account`: 计费账户（如果需要）
- `modules`: 需要加载的环境模块

### 第三步：验证环境

```bash
# 检查 WRF/WPS 是否可访问
ls -l $(python3 -c "import json; print(json.load(open('config/wrf_env.json'))['wrf_root'])")/main/wrf.exe

# 检查 WPS_GEOG 数据
ls -l $(python3 -c "import json; print(json.load(open('config/wrf_env.json'))['geog_data_path'])")

# 测试初始化（dry-run）
python3 scripts/wrf.py init --project-name test_init --dry-run
```

---

## 使用指南

### 方式一：通过 Claude Code（推荐）

#### 1. 打开项目

在 Claude Code 中打开 wrf-skill 目录：

```bash
# 在终端中
cd wrf-skill
code .  # 或使用 Claude Code 打开
```

#### 2. 与 AI 对话

Claude 会自动识别 WRF 技能，你可以直接用自然语言交互：

**初始化项目：**
```
你: 帮我初始化一个名为 "typhoon_case" 的 WRF 项目
```

**配置模拟：**
```
你: 配置一个台风模拟：
- 区域：东海和台湾海峡（120-130°E, 20-30°N）
- 分辨率：外层 9km，内层 3km
- 时间：2024年8月1日00时到8月3日00时
- 资料：GFS
- 物理方案：Thompson 微物理，RRTMG 辐射，YSU 边界层
```

**运行工作流：**
```
你: 下载 GFS 资料并运行 WPS 预处理
```

```
你: 提交 WRF 模拟到 HPC 集群
```

**查看状态：**
```
你: 检查模拟运行状态
```

**后处理：**
```
你: 生成以下图形：
1. 地面温度和风场
2. 850hPa 温度和风场
3. 累积降水
4. 沿 25°N 的垂直剖面
```

### 方式二：命令行使用

#### 完整工作流示例

```bash
# ========== 1. 初始化项目 ==========
python3 scripts/wrf.py init --project-name my_case

# ========== 2. 配置模拟 ==========
# 方式 A: 使用自然语言描述
python3 scripts/wrf.py config \
  --project-name my_case \
  --request-text "华东地区，中心 120E 30N，外层 9km 内层 3km，GFS 资料，2024-07-20 00:00 到 2024-07-22 00:00，本地运行"

# 方式 B: 使用命令行参数
python3 scripts/wrf.py config \
  --project-name my_case \
  --center-lon 120.0 \
  --center-lat 30.0 \
  --domain-size 500 \
  --resolution 9 \
  --start-time "2024-07-20 00:00:00" \
  --end-time "2024-07-22 00:00:00" \
  --forcing-source gfs \
  --run-mode local

# ========== 3. 下载气象资料 ==========
python3 scripts/wrf.py data --project-name my_case

# 查看下载进度
python3 scripts/wrf.py status --project-name my_case

# ========== 4. 运行 WPS 预处理 ==========
python3 scripts/wrf.py wps --project-name my_case

# 等待 WPS 完成
python3 scripts/wrf.py status --project-name my_case

# ========== 5. 运行 WRF 模拟 ==========
# 本地运行
python3 scripts/wrf.py run --project-name my_case

# 或提交到 HPC（如果配置了 HPC）
python3 scripts/wrf.py run --project-name my_case --run-mode hpc

# ========== 6. 监控运行状态 ==========
# 查看状态
python3 scripts/wrf.py status --project-name my_case

# 查看日志
python3 scripts/wrf.py logs --project-name my_case

# 如果是 HPC 作业，收集输出
python3 scripts/wrf.py collect --project-name my_case

# ========== 7. 后处理和可视化 ==========
# 生成后处理配置
python3 scripts/post_spec.py --project-name my_case --output post_spec.json

# 或使用完整示例
cp templates/post_spec.example.json post_spec.json

# 运行后处理
python3 scripts/wrf.py post --project-name my_case --post-spec post_spec.json

# 或渲染单个图形
python3 scripts/plot_wrfout.py \
  --wrfout runs/my_case/wrf/wrfout_d01_2024-07-20_00:00:00 \
  --figure-id surface_temperature \
  --post-spec post_spec.json \
  --out output/temperature.png
```

#### 常用命令速查

```bash
# 查看帮助
python3 scripts/wrf.py --help
python3 scripts/wrf.py <command> --help

# 查看版本
python3 scripts/wrf.py --version

# 列出所有项目
ls runs/

# 查看项目状态
cat runs/my_case/project.json

# 取消正在运行的任务
python3 scripts/wrf.py cancel --project-name my_case

# 清理临时文件
python3 scripts/wrf.py cleanup --dry-run  # 预览
python3 scripts/wrf.py cleanup            # 执行清理
```

### 方式三：在 Codex 中使用

#### 1. 安装 Codex 插件

```bash
# 方式 A: 直接在 Codex 中打开这个仓库（推荐）
cd wrf-skill
# 然后在 Codex 中打开此目录

# 方式 B: 全局安装
bash scripts/install_codex_skills.sh
```

#### 2. 创建工作区

在 Codex 中对话：

```
你: 用 wrf-workspace-init 在 ~/wrf-projects/my-workspace 创建一个新的工作区
```

或使用命令行：

```bash
bash ~/.codex/skills/wrf-workspace-init/scripts/init_workspace.sh \
  --target-root ~/wrf-projects/my-workspace
```

#### 3. 在工作区中工作

```bash
cd ~/wrf-projects/my-workspace
# 在 Codex 中打开这个目录
```

然后就可以像使用 Claude Code 一样与 Codex 对话了。

---

## 进阶使用

---

## 主要功能

### 🤖 AI 驱动的配置生成

用自然语言描述你的模拟需求，AI 会自动生成正确的配置文件：

```bash
python3 scripts/wrf.py config \
  --project-name demo \
  --request-text "长三角地区，3km 分辨率，ERA5 资料，2024-08-01 到 2024-08-03"
```

### 📦 自动资料下载

支持主流气象资料源：
- **GFS**: 全球预报系统（0.25° 分辨率）
- **FNL**: NCEP 最终分析场（1° 分辨率）
- **ERA5**: ECMWF 再分析资料（0.25° 分辨率）

### 🖥️ 灵活的运行模式

- **本地模式**: 在本机直接运行
- **HPC 模式**: 自动生成作业脚本并提交到 Slurm/PBS 调度器

### 📊 强大的后处理

使用 `post_spec.json` 定义可视化需求：
- 地图视图（温度、风场、降水等）
- 垂直剖面（时间-高度、时间-气压）
- 路径剖面（任意路径的垂直结构）
- 矢量场叠加（风场、环流）

```bash
# 生成后处理配置模板
python3 scripts/post_spec.py --project-name demo --output post_spec.json

# 或使用完整示例
cp templates/post_spec.example.json post_spec.json

# 渲染指定图形
python3 scripts/plot_wrfout.py \
  --wrfout runs/demo/wrf/wrfout_d01_2024-07-20_00:00:00 \
  --figure-id surface_temperature \
  --post-spec post_spec.json \
  --out temperature.png
```

---

## 实用工具

### 清理临时文件

```bash
# 预览将要清理的内容
python3 scripts/wrf.py cleanup --dry-run

# 清理临时目录
python3 scripts/wrf.py cleanup

# 清理超过 48 小时的过期项目
python3 scripts/wrf.py cleanup --include-stale --max-age 48
```

### 查看版本

```bash
python3 scripts/wrf.py --version
```

---

## 项目结构

```
wrf-skill/
├── scripts/           # 核心工作流脚本
│   ├── wrf.py        # 统一命令行入口
│   ├── wrf_init.py   # 项目初始化
│   ├── wrf_config.py # 配置生成
│   ├── wrf_data.py   # 资料下载
│   ├── wrf_wps.py    # WPS 预处理
│   ├── wrf_run.py    # WRF 运行
│   ├── wrf_post.py   # 后处理
│   └── cleanup.py    # 清理工具
├── config/            # 配置文件
│   ├── wrf_env.json  # 运行环境配置（需自行创建）
│   ├── domains_presets.json    # 区域预设
│   ├── physics_schemes.json    # 物理方案
│   └── post_schema.json        # 后处理规范
├── templates/         # 配置模板
├── runs/             # 模拟项目目录
└── docs/             # 文档
```

---

## 配置说明

### 本地运行配置

创建 `config/wrf_env.json`：

```json
{
  "wrf_root": "/path/to/WRF",
  "wps_root": "/path/to/WPS",
  "geog_data_path": "/path/to/WPS_GEOG"
}
```

### HPC 运行配置

从示例开始：

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

编辑关键字段：

```json
{
  "wrf_root": "/path/to/WRF",
  "wps_root": "/path/to/WPS",
  "geog_data_path": "/path/to/WPS_GEOG",
  "hpc": {
    "backend": "slurm",
    "remote_host": "your-hpc-login-node",
    "remote_project_root": "/scratch/username/wrf-projects",
    "runtime": {
      "mode": "mpirun",
      "wrf_nproc": 48,
      "partition": "compute",
      "walltime": "06:00:00"
    }
  }
}
```

---

## 在 Codex 中使用

这个仓库包含 Codex 插件，可以直接在 Codex 中使用：

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill

# 方式 1: 直接在 Codex 中打开这个仓库（推荐）
# Codex 会自动发现 .agents/plugins/marketplace.json

# 方式 2: 全局安装到 ~/.codex/skills/
bash scripts/install_codex_skills.sh
```

然后对 Codex 说：
- "用 wrf-workspace-init 创建一个新的工作区"
- "帮我配置一个台风模拟"

---

## 后处理协议

WRF Skill 使用 `schema_version=3` 的后处理规范，支持：

### 数据层定义 (layer_defs)
- `wrf_native_2d`: 2D 原生变量
- `wrf_native_3d`: 3D 原生变量（带层选择）
- `wrf_diag`: 诊断量（风速、风向、相对湿度等）

### 视图类型 (view_defs)
- 地图视图: `west_east × south_north`
- 时间剖面: `time-x`, `time-y`
- 垂直剖面: `time-height`, `time-pressure`
- 路径剖面: `distance_km × height_m/pressure_hpa`

### 样式定义 (style_defs)
- 栅格填色 (raster)
- 等值线 (contour)
- 分类填色 (categorical)
- 矢量场 (vector/quiver)

完整文档见 `docs/post_runtime_v3.zh-CN.md`。

---

## 常见问题

**Q: 我需要自己编译 WRF 吗？**  
A: 是的，这个工具假设你已经有可用的 WRF/WPS 环境。

**Q: 支持哪些气象资料？**  
A: 目前内置支持 GFS、FNL 和 ERA5。其他资料源需要自行集成。

**Q: 可以在 Windows 上运行吗？**  
A: 需要 WSL（Windows Subsystem for Linux）环境。

**Q: HPC 模式支持哪些调度器？**  
A: 支持 Slurm 和 PBS/Torque。

**Q: 如何贡献代码？**  
A: 欢迎提交 Pull Request！请先阅读贡献指南。

---

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/

# 代码检查
ruff check scripts/

# 类型检查
mypy scripts/
```

---

## 许可证

本项目采用 [Apache-2.0](LICENSE) 许可证。

第三方文件说明见 [THIRD_PARTY.md](THIRD_PARTY.md)。

---

## 致谢

感谢 WRF 和 WPS 开发团队提供强大的数值模式工具。

---

## 联系方式

- 问题反馈: [GitHub Issues](https://github.com/origin652/wrf-skill/issues)
- 项目主页: [https://github.com/origin652/wrf-skill](https://github.com/origin652/wrf-skill)
