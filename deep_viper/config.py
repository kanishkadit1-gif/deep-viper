import os
import re
import yaml
from pathlib import Path
from dataclasses import dataclass


def _load_dotenv(repo_root: Path) -> None:
    """Minimal .env loader (no dependency): KEY=VALUE lines into os.environ."""
    env_path = repo_root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _expand_env(obj):
    """Recursively expand ${VAR} placeholders in strings using os.environ."""
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    if isinstance(obj, str):
        return re.sub(r"\$\{([^}]+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), obj)
    return obj


@dataclass
class VLMConfig:
    base_url: str
    model: str
    timeout: int
    temperature: float
    api_key: str
    max_tokens: int = 4096


@dataclass
class PlanningConfig:
    max_iterations: int
    num_trajectories: int
    convergence_risk_threshold: float
    acceptable_risk_threshold: float
    arrival_threshold_px: int
    compass_directions: int
    # Two-phase explore->refine loop (v4.3). Defaults keep old behavior if omitted.
    explore_iterations: int = 5
    refine_iterations: int = 3
    optimality_wp_weight: float = 0.5
    optimality_len_weight: float = 0.5


@dataclass
class LoggingConfig:
    runs_dir: str
    save_all_iterations: bool


@dataclass
class LangSmithConfig:
    project_name: str
    tracing: bool


@dataclass
class Config:
    vlm: VLMConfig
    planning: PlanningConfig
    logging: LoggingConfig
    langsmith: LangSmithConfig


def load_config(path: str | Path = None, vlm_profile: str | None = None) -> Config:
    repo_root = Path(__file__).parent.parent
    if path is None:
        path = repo_root / "config.yaml"
    _load_dotenv(repo_root)
    with open(path) as f:
        raw = _expand_env(yaml.safe_load(f))

    # Resolve which VLM backend to use:
    #   explicit arg > config's vlm_profile > legacy top-level `vlm:` block
    profiles = raw.get("vlm_profiles", {})
    selected = vlm_profile or raw.get("vlm_profile")
    if selected and selected in profiles:
        vlm_raw = profiles[selected]
        print(f"[Config] VLM profile: '{selected}' ({vlm_raw.get('model')} @ {vlm_raw.get('base_url')})")
    elif selected and selected not in profiles:
        raise ValueError(
            f"Unknown vlm profile '{selected}'. Available: {list(profiles) or '(none)'}"
        )
    else:
        vlm_raw = raw["vlm"]

    return Config(
        vlm=VLMConfig(**vlm_raw),
        planning=PlanningConfig(**raw["planning"]),
        logging=LoggingConfig(**raw["logging"]),
        langsmith=LangSmithConfig(**raw["langsmith"]),
    )
