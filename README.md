# WRF Skill

AI-powered workflow tools for running WRF simulations with ease.

English | [简体中文](README.zh-CN.md)

---

## What is this?

WRF Skill is a workflow toolkit that enables Claude Code and Codex to help you operate the WRF model. If you already have a compiled WRF/WPS environment, this tool allows AI assistants to:

- 🚀 Quickly initialize simulation projects
- ⚙️ Automatically generate configuration files (namelist.wps, namelist.input)
- 📦 Download and prepare meteorological data (GFS, FNL, ERA5)
- 🔄 Run complete WPS → WRF workflows
- 📊 Post-process and visualize output results
- 🖥️ Support both local execution and HPC cluster submission

**What this is NOT:** This is not a WRF installer or compilation tool. You need to prepare your own WRF/WPS runtime environment.

---

## Installation Guide

### System Requirements

#### Required Environment
- **Operating System**: Linux or WSL2 (Windows Subsystem for Linux 2)
- **Python**: 3.10 or higher
- **WRF/WPS**: Compiled and runnable version (WRF 4.x recommended)
- **Geographic Data**: Complete WPS_GEOG dataset
- **Storage**: At least 50GB available space (for simulation outputs)

#### Python Dependencies
- netCDF4 >= 1.6.0
- numpy >= 1.24.0
- matplotlib >= 3.7.0
- cartopy >= 0.22.0
- xarray >= 2023.1.0

### Step 1: Install WRF Skill

```bash
# 1. Clone the repository
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill

# 2. Install core dependencies
pip install -e .

# 3. (Optional) For development or running tests
pip install -e ".[dev]"

# 4. Verify installation
python3 scripts/wrf.py --version
# Should output: wrf-skill v0.1.0
```

### Step 2: Configure Runtime Environment

#### Local Execution Configuration

Create `config/wrf_env.json` file:

```bash
# Copy template (if available) or create manually
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

**Configuration Explanation:**
- `wrf_root`: WRF installation directory (containing `main/wrf.exe`)
- `wps_root`: WPS installation directory (containing `geogrid.exe`, etc.)
- `geog_data_path`: WPS_GEOG geographic data path
- `wrf_nproc`: Number of CPU cores for local execution

#### HPC Cluster Configuration

If you want to run on an HPC cluster:

```bash
# 1. Start from example configuration
cp config/wrf_env.hpc.example.json config/wrf_env.json

# 2. Edit configuration file
nano config/wrf_env.json
```

**HPC Configuration Example (Slurm):**

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

**HPC Configuration Explanation:**
- `backend`: Scheduler type (`slurm` or `pbs`)
- `remote_host`: HPC login node address
- `remote_project_root`: Project root directory on cluster
- `scheduler_ssh_cmd`: SSH connection command (optional, defaults to `ssh`)
- `wrf_nproc`: Number of MPI processes
- `partition`: Job queue/partition name
- `walltime`: Maximum runtime
- `account`: Billing account (if required)
- `modules`: Environment modules to load

### Step 3: Verify Environment

```bash
# Check if WRF/WPS are accessible
ls -l $(python3 -c "import json; print(json.load(open('config/wrf_env.json'))['wrf_root'])")/main/wrf.exe

# Check WPS_GEOG data
ls -l $(python3 -c "import json; print(json.load(open('config/wrf_env.json'))['geog_data_path'])")

# Test initialization (dry-run)
python3 scripts/wrf.py init --project-name test_init --dry-run
```

---

## Usage Guide

### Method 1: Using Claude Code (Recommended)

#### 1. Open Project

Open the wrf-skill directory in Claude Code:

```bash
# In terminal
cd wrf-skill
code .  # Or open with Claude Code
```

#### 2. Talk to AI

Claude will automatically recognize WRF skills, and you can interact in natural language:

**Initialize Project:**
```
You: Help me initialize a WRF project named "typhoon_case"
```

**Configure Simulation:**
```
You: Configure a typhoon simulation:
- Region: East China Sea and Taiwan Strait (120-130°E, 20-30°N)
- Resolution: 9km outer domain, 3km inner domain
- Time: August 1, 2024 00:00 to August 3, 2024 00:00
- Data: GFS
- Physics: Thompson microphysics, RRTMG radiation, YSU PBL
```

**Run Workflow:**
```
You: Download GFS data and run WPS preprocessing
```

```
You: Submit WRF simulation to HPC cluster
```

**Check Status:**
```
You: Check simulation status
```

**Post-processing:**
```
You: Generate the following plots:
1. Surface temperature and wind
2. 850hPa temperature and wind
3. Accumulated precipitation
4. Vertical cross-section along 25°N
```

### Method 2: Command Line Usage

#### Complete Workflow Example

```bash
# ========== 1. Initialize Project ==========
python3 scripts/wrf.py init --project-name my_case

# ========== 2. Configure Simulation ==========
# Option A: Using natural language description
python3 scripts/wrf.py config \
  --project-name my_case \
  --request-text "East China, center 120E 30N, 9km outer 3km inner, GFS data, 2024-07-20 00:00 to 2024-07-22 00:00, local mode"

# Option B: Using command line parameters
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

# ========== 3. Download Meteorological Data ==========
python3 scripts/wrf.py data --project-name my_case

# Check download progress
python3 scripts/wrf.py status --project-name my_case

# ========== 4. Run WPS Preprocessing ==========
python3 scripts/wrf.py wps --project-name my_case

# Wait for WPS completion
python3 scripts/wrf.py status --project-name my_case

# ========== 5. Run WRF Simulation ==========
# Local execution
python3 scripts/wrf.py run --project-name my_case

# Or submit to HPC (if configured)
python3 scripts/wrf.py run --project-name my_case --run-mode hpc

# ========== 6. Monitor Status ==========
# Check status
python3 scripts/wrf.py status --project-name my_case

# View logs
python3 scripts/wrf.py logs --project-name my_case

# For HPC jobs, collect outputs
python3 scripts/wrf.py collect --project-name my_case

# ========== 7. Post-processing and Visualization ==========
# Generate post-processing configuration
python3 scripts/post_spec.py --project-name my_case --output post_spec.json

# Or use complete example
cp templates/post_spec.example.json post_spec.json

# Run post-processing
python3 scripts/wrf.py post --project-name my_case --post-spec post_spec.json

# Or render individual figures
python3 scripts/plot_wrfout.py \
  --wrfout runs/my_case/wrf/wrfout_d01_2024-07-20_00:00:00 \
  --figure-id surface_temperature \
  --post-spec post_spec.json \
  --out output/temperature.png
```

#### Quick Command Reference

```bash
# View help
python3 scripts/wrf.py --help
python3 scripts/wrf.py <command> --help

# Check version
python3 scripts/wrf.py --version

# List all projects
ls runs/

# View project status
cat runs/my_case/project.json

# Cancel running task
python3 scripts/wrf.py cancel --project-name my_case

# Clean up temporary files
python3 scripts/wrf.py cleanup --dry-run  # Preview
python3 scripts/wrf.py cleanup            # Execute cleanup
```

### Method 3: Using with Codex

#### 1. Install Codex Plugin

```bash
# Option A: Open this repository directly in Codex (recommended)
cd wrf-skill
# Then open this directory in Codex

# Option B: Global installation
bash scripts/install_codex_skills.sh
```

#### 2. Create Workspace

In Codex conversation:

```
You: Use wrf-workspace-init to create a new workspace at ~/wrf-projects/my-workspace
```

Or use command line:

```bash
bash ~/.codex/skills/wrf-workspace-init/scripts/init_workspace.sh \
  --target-root ~/wrf-projects/my-workspace
```

#### 3. Work in Workspace

```bash
cd ~/wrf-projects/my-workspace
# Open this directory in Codex
```

Then you can interact with Codex just like using Claude Code.

---

## Advanced Usage

---

## Key Features

### 🤖 AI-Driven Configuration Generation

Describe your simulation needs in natural language, and AI will automatically generate the correct configuration files:

```bash
python3 scripts/wrf.py config \
  --project-name demo \
  --request-text "Yangtze River Delta, 3km resolution, ERA5 data, 2024-08-01 to 2024-08-03"
```

### 📦 Automatic Data Download

Supports mainstream meteorological data sources:
- **GFS**: Global Forecast System (0.25° resolution)
- **FNL**: NCEP Final Analysis (1° resolution)
- **ERA5**: ECMWF Reanalysis (0.25° resolution)

### 🖥️ Flexible Execution Modes

- **Local mode**: Run directly on your machine
- **HPC mode**: Automatically generate job scripts and submit to Slurm/PBS schedulers

### 📊 Powerful Post-Processing

Define visualization needs using `post_spec.json`:
- Map views (temperature, wind, precipitation, etc.)
- Vertical cross-sections (time-height, time-pressure)
- Path cross-sections (vertical structure along arbitrary paths)
- Vector field overlays (wind, circulation)
- Native statistical charts in `schema_version=4`
  - time-series line charts
  - grouped bar charts
  - grouped/time boxplots

```bash
# Generate post-processing configuration template
python3 scripts/post_spec.py --project-name demo --output post_spec.json

# Or use the complete example
cp templates/post_spec.example.json post_spec.json

# Run project-level post-processing for figures and charts
python3 scripts/wrf.py post --project-name demo --post-spec post_spec.json

# Render specific figures
python3 scripts/plot_wrfout.py \
  --wrfout runs/demo/wrf/wrfout_d01_2024-07-20_00:00:00 \
  --figure-id surface_temperature \
  --post-spec post_spec.json \
  --out temperature.png
```

---

## Utilities

### Clean Up Temporary Files

```bash
# Preview what will be cleaned
python3 scripts/wrf.py cleanup --dry-run

# Clean temporary directories
python3 scripts/wrf.py cleanup

# Clean stale projects older than 48 hours
python3 scripts/wrf.py cleanup --include-stale --max-age 48
```

### Check Version

```bash
python3 scripts/wrf.py --version
```

---

## Project Structure

```
wrf-skill/
├── scripts/           # Core workflow scripts
│   ├── wrf.py        # Unified CLI entry point
│   ├── wrf_init.py   # Project initialization
│   ├── wrf_config.py # Configuration generation
│   ├── wrf_data.py   # Data download
│   ├── wrf_wps.py    # WPS preprocessing
│   ├── wrf_run.py    # WRF execution
│   ├── wrf_post.py   # Post-processing
│   └── cleanup.py    # Cleanup utility
├── config/            # Configuration files
│   ├── wrf_env.json  # Runtime environment config (create yourself)
│   ├── domains_presets.json    # Domain presets
│   ├── physics_schemes.json    # Physics schemes
│   └── post_schema.json        # Post-processing schema
├── templates/         # Configuration templates
├── runs/             # Simulation project directories
└── docs/             # Documentation
```

---

## Configuration

### Local Execution Configuration

Create `config/wrf_env.json`:

```json
{
  "wrf_root": "/path/to/WRF",
  "wps_root": "/path/to/WPS",
  "geog_data_path": "/path/to/WPS_GEOG"
}
```

### HPC Execution Configuration

Start from the example:

```bash
cp config/wrf_env.hpc.example.json config/wrf_env.json
```

Edit key fields:

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

## Using with Codex

This repository includes a Codex plugin for direct use:

```bash
git clone https://github.com/origin652/wrf-skill.git
cd wrf-skill

# Method 1: Open this repository directly in Codex (recommended)
# Codex will automatically discover .agents/plugins/marketplace.json

# Method 2: Install globally to ~/.codex/skills/
bash scripts/install_codex_skills.sh
```

Then tell Codex:
- "Use wrf-workspace-init to create a new workspace"
- "Help me configure a typhoon simulation"

---

## Post-Processing Protocol

WRF Skill uses `schema_version=4` post-processing specification for new chart workflows while keeping `schema_version=3` figure-only specs compatible.

### Layer Definitions (layer_defs)
- `wrf_native_2d`: 2D native variables
- `wrf_native_3d`: 3D native variables (with level selection)
- `wrf_diag`: Diagnostic quantities (wind speed, direction, relative humidity, etc.)

### View Types (view_defs)
- Map views: `west_east × south_north`
- Time cross-sections: `time-x`, `time-y`
- Vertical cross-sections: `time-height`, `time-pressure`
- Path cross-sections: `distance_km × height_m/pressure_hpa`

### Style Definitions (style_defs)
- Raster fill (raster)
- Contour lines (contour)
- Categorical fill (categorical)
- Vector fields (vector/quiver)

### Region Definitions (region_defs, v4)
- Grid-window region grouping with `bottom_top`, `south_north`, and `west_east`
- `index_range` selectors use `[start, stop)` semantics

### Statistical Charts (charts, v4)
- `line + time`: regional time-series such as area-mean temperature
- `bar + group`: grouped last-frame comparisons across named regions
- `boxplot + time/group`: spatial distributions by time or time-distribution by region

Full documentation: `docs/post_runtime_v3.md`.

---

## FAQ

**Q: Do I need to compile WRF myself?**  
A: Yes, this tool assumes you already have a working WRF/WPS environment.

**Q: Which meteorological data sources are supported?**  
A: Currently built-in support for GFS, FNL, and ERA5. Other sources require custom integration.

**Q: Can I run this on Windows?**  
A: You need WSL (Windows Subsystem for Linux).

**Q: Which schedulers does HPC mode support?**  
A: Slurm and PBS/Torque are supported.

**Q: How can I contribute?**  
A: Pull requests are welcome! Please read the contribution guidelines first.

---

## Development

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Code linting
ruff check scripts/

# Type checking
mypy scripts/
```

---

## License

This project is licensed under [Apache-2.0](LICENSE).

Third-party files are documented in [THIRD_PARTY.md](THIRD_PARTY.md).

---

## Acknowledgments

Thanks to the WRF and WPS development teams for providing powerful numerical modeling tools.

---

## Contact

- Issue tracker: [GitHub Issues](https://github.com/origin652/wrf-skill/issues)
- Project homepage: [https://github.com/origin652/wrf-skill](https://github.com/origin652/wrf-skill)
