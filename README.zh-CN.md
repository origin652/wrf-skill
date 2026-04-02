# wrf-skill 中文说明

[Back to English README](README.md)

## 这是什么

`wrf-skill` 不是 WRF 本体，也不是一个自带完整 WRF/WPS 编译产物的发行版。

它的定位是一个面向用户和 Agent 工作流的 WRF 流程工具层，用来把这些原本分散的事情串起来：

- 初始化项目目录
- 根据自然语言或结构化配置生成 `simulation_spec.json`
- 渲染 `namelist.wps` 和 `namelist.input`
- 异步运行 `wrf-data`、`wrf-wps`、`wrf-run`
- 在 HPC 提交前做 admission 检查
- 统一查询状态、日志、取消任务、回收结果
- 打包成一个干净的 bundle 给别人安装

## 它的优势是什么

和手工维护一堆 shell 脚本、namelist、运行目录相比，这个仓库的优势主要是：

- 少手工操作：项目结构、配置文件和产物登记都由工具统一管理。
- 长任务不阻塞：下载数据、WPS、WRF 都走异步任务层，不需要 AI 一直挂着等。
- HPC 更稳：提交前先做 admission，而不是直接盲投作业。
- 路径更灵活：支持本地跑、已在登录节点上跑、以及本地 SSH 到登录节点再投递。
- 状态更清晰：进度和产物统一写进 `runs/<project>/project.json`。
- 更方便分发：可以只打包必要文件，不把私密配置和大数据一起发出去。

## 安装方式

### 方式一：直接用源码仓库

前提是你自己已经准备好了 WRF/WPS 运行环境；这个仓库不会自带编译好的 WRF/WPS，也不会自带完整地理数据。

一般至少需要：

- Python 3.10+
- 独立准备好的 WRF/WPS 安装
- 运行所需 geog 数据和表文件
- 如果要跑集群，还需要自己的 HPC 访问环境

获取源码后：

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill
```

如果要用 HPC，先从示例配置复制一份本地配置：

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

然后按你的集群环境填写。

### 方式二：从 bundle 安装

如果你拿到的是打包产物，而不是整个源码仓库，可以这样安装：

```bash
python3 scripts/install_skill_bundle.py --target /path/to/install-root
```

如果你自己要制作 bundle：

```bash
python3 scripts/package_skill_bundle.py --output dist/wrf-skill-bundle.tar.gz
```

bundle 默认不会包含：

- 私密配置
- `runs/` 输出
- `WPS_GEOG`
- 编译产物

## 第三方文件说明

仓库目前包含少量 WPS 支持表，位置在 `third_party/wps-support/`。来源和发布注意事项见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 许可证

仓库中由项目自行编写的文件按 [Apache-2.0](LICENSE) 发布。第三方支持文件的说明单独见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 如何使用

### 1. 初始化项目

```bash
python3 scripts/wrf_init.py --project-name demo
```

### 2. 生成配置

```bash
python3 scripts/wrf_config.py   --project-name demo   --request-text "East China, GFS, 2024-07-20 00:00 to 2024-07-20 12:00, local"   --run-mode local
```

### 3. 启动长任务

```bash
python3 scripts/wrf_task.py start --project-name demo --step wrf-data
python3 scripts/wrf_task.py start --project-name demo --step wrf-wps
python3 scripts/wrf_task.py start --project-name demo --step wrf-run
```

### 4. 查看当前进度

```bash
python3 scripts/wrf_task.py status --project-name demo
python3 scripts/wrf_task.py logs --project-name demo --lines 80
```

## HPC 怎么用

这个仓库把“运行模式”和“访问路径”拆开了。

- `run_mode=local`：直接在当前机器运行
- `run_mode=hpc` + `access_mode=login`：当前机器本身就是登录节点
- `run_mode=hpc` + `access_mode=ssh`：当前机器需要先 SSH 到登录节点，再提交调度器

当前内置适配器：

- Slurm
- PBS

如果你要走 HPC，建议从 [config/wrf_env.hpc.example.json](config/wrf_env.hpc.example.json) 开始配置。

## 典型流程

```text
wrf_init -> wrf_config -> wrf_task start wrf-data -> wrf_task start wrf-wps -> wrf_task start wrf-run
```

长任务默认是异步的，所以通常是“启动后返回，后面再查状态和日志”。

## 仓库不包含什么

为了方便公开发布，这个仓库默认不带：

- 真实的 `config/wrf_env.json`
- `runs/` 下的模拟输出
- 完整 `WPS_GEOG`
- 编译好的 WRF/WPS 目录
- 私有 SSH / 集群凭证

## 当前适合的使用场景

当前更适合这些场景：

- 想把 WRF 工作流标准化
- 想让 AI/Agent 帮你组织配置和任务
- 想把本地与 HPC 的运行流程统一起来
- 想把自己的 skill/workflow 干净地分发给别人

如果你只是想要一个“一键自带全部 WRF/WPS 二进制和地理数据”的安装包，这个仓库现在还不是那个方向。
