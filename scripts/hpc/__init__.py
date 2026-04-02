from __future__ import annotations

from typing import Any

from .base import HpcSchedulerAdapter, resolve_backend_name
from .pbs import PbsSchedulerAdapter
from .slurm import SlurmSchedulerAdapter

_ADAPTERS: dict[str, type[HpcSchedulerAdapter]] = {
    "slurm": SlurmSchedulerAdapter,
    "pbs": PbsSchedulerAdapter,
}


def register_scheduler_adapter(name: str, adapter_cls: type[HpcSchedulerAdapter]) -> None:
    _ADAPTERS[name.lower()] = adapter_cls


def get_scheduler_adapter(config_or_backend: dict[str, Any] | str) -> HpcSchedulerAdapter:
    backend = (
        resolve_backend_name(config_or_backend)
        if isinstance(config_or_backend, dict)
        else str(config_or_backend).strip().lower()
    )
    if backend not in _ADAPTERS:
        raise KeyError(f"Unsupported HPC backend: {backend}")
    return _ADAPTERS[backend]()
