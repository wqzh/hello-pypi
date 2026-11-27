import os
import re
from pathlib import Path
from urllib.parse import urlparse


# ============================================================
# 加载 .env 文件（优先），否则使用环境变量
# ------------------------------------------------------------
# 支持三套写法，可混用，结果会合并去重：
#
# 1) 默认模型（沿用旧写法，最常用）
#       PI_LLM_BASE_URL / PI_LLM_MODEL_ID / PI_LLM_API_KEY
#       PI_LLM_PROVIDER / PI_LLM_MODEL_NAME / PI_LLM_API_TYPE
#       PI_LLM_CONTEXT_WINDOW / PI_LLM_MAX_TOKENS / PI_LLM_REASONING
#
# 2) 命名模型组：在 PI_LLM_ 后加一个组名再跟字段名
#       PI_LLM_MEITUAN_BASE_URL / PI_LLM_MEITUAN_MODEL_ID
#       PI_LLM_MEITUAN_API_KEY / PI_LLM_MEITUAN_PROVIDER
#       PI_LLM_MEITUAN_MODEL_NAME / PI_LLM_MEITUAN_CONTEXT_WINDOW ...
#   组名任意（字母/数字/下划线），会自动被发现，无需额外声明。
#   未写的字段回落到第 1 套的全局值（如 CONTEXT_WINDOW / MAX_TOKENS）。
#
# 3) 显式模型列表（可选，用于精确控制顺序）
#       PI_MODELS=qwen35,deepseek
#       PI_MODEL_QWEN35_NAME=... / _MODEL_ID / _BASE_URL / _API_KEY ...
#
# 前端下拉显示「供应商/模型名」：供应商 = PROVIDER（为空取 BASE_URL 主机名），
# 模型名 = NAME（为空取 MODEL_ID）。
# 可用 PI_DEFAULT_MODEL 指定默认选中的模型键（组名小写 或 PI_MODELS 中的键）。
# ============================================================

_DOTENV_LOADED = False

_DEFAULTS = {
    "api_type": "openai-completions",
    "context_window": 54000,
    "max_tokens": 8192,
    "reasoning": False,
}

#: 字段名按长度倒序排列，避免 MODEL_NAME / MODEL_ID 被短后缀抢先匹配。
_FIELD_RE = (
    "MODEL_NAME|MODEL_ID|BASE_URL|API_KEY|PROVIDER|API_TYPE"
    "|CONTEXT_WINDOW|MAX_TOKENS|REASONING"
)
#: 例：PI_LLM_BASE_URL → group="", PI_LLM_MEITUAN_BASE_URL → group="MEITUAN"
_LLM_ENV_RE = re.compile(rf"^PI_LLM_(?:(?P<group>[A-Z0-9_]+)_)?(?P<field>{_FIELD_RE})$")


def _load_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv

        # override=False：真实环境变量优先于 .env
        load_dotenv(env_path, override=False)
        print(f"[INFO] 已加载 .env 文件: {env_path}")
    except ImportError:
        print("[WARN] python-dotenv 未安装，跳过 .env 加载。")
        print("[HINT] 运行: pip install python-dotenv")


def _env_prefix(key: str) -> str:
    """模型键 → 环境变量前缀，例如 ``qwen-35`` → ``QWEN_35``。"""
    return re.sub(r"[^0-9A-Za-z]+", "_", key).strip("_").upper()


def _as_int(value, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _get(env: dict, field: str) -> str:
    value = env.get(field, "")
    return (value or "").strip()


def _make_entry(
    *,
    key: str,
    model_id: str,
    name: str,
    provider: str,
    base_url: str,
    api_key: str,
    api_type: str,
    context_window: int,
    max_tokens: int,
    reasoning: bool,
) -> dict:
    host = urlparse(base_url).hostname or ""
    provider_label = provider.strip() or host or "unknown"
    model_label = (name or "").strip() or model_id
    return {
        "key": key,
        "label": f"{provider_label}/{model_label}",
        "id": model_id,
        "name": model_label,
        "provider": provider_label,
        "base_url": base_url,
        "api": api_type or _DEFAULTS["api_type"],
        "api_key": api_key,
        "context_window": context_window,
        "max_tokens": max_tokens,
        "reasoning": reasoning,
    }


def _entry_from_env(
    env: dict,
    *,
    key: str,
    global_env: dict | None = None,
) -> dict | None:
    """由一组字段构造模型；缺 MODEL_ID / BASE_URL 则返回 None。"""
    base = global_env if global_env is not None else env

    def pick(field: str) -> str:
        value = _get(env, field)
        if not value and base is not env:
            value = _get(base, field)
        return value

    model_id = _get(env, "MODEL_ID")
    base_url = _get(env, "BASE_URL")
    if not model_id or not base_url:
        return None

    api_key = pick("API_KEY")

    return _make_entry(
        key=key,
        model_id=model_id,
        name=pick("MODEL_NAME"),
        provider=pick("PROVIDER"),
        base_url=base_url,
        api_key=api_key,
        api_type=pick("API_TYPE"),
        context_window=_as_int(pick("CONTEXT_WINDOW"), _DEFAULTS["context_window"]),
        max_tokens=_as_int(pick("MAX_TOKENS"), _DEFAULTS["max_tokens"]),
        reasoning=_as_bool(pick("REASONING"), _DEFAULTS["reasoning"]),
    )


def _scan_llm_groups() -> dict[str, dict[str, str]]:
    """扫描环境变量，按 ``PI_LLM_<GROUP>_<FIELD>`` 分组收集配置。"""
    groups: dict[str, dict[str, str]] = {}
    for name, value in os.environ.items():
        match = _LLM_ENV_RE.match(name)
        if not match:
            continue
        group = (match.group("group") or "").lower()
        groups.setdefault(group, {})[match.group("field")] = value
    return groups


def _entry_from_explicit_list(seen: set) -> list[dict]:
    """解析 PI_MODELS + PI_MODEL_<KEY>_* （显式声明，决定先后顺序）。"""
    models: list[dict] = []
    raw_keys = (os.getenv("PI_MODELS", "") or "").strip()
    for raw_key in re.split(r"[,\s]+", raw_keys):
        raw_key = raw_key.strip()
        if not raw_key:
            continue
        prefix = f"PI_MODEL_{_env_prefix(raw_key)}_"
        env = {
            field: os.getenv(f"{prefix}{field}", "") or ""
            for field in (
                "MODEL_NAME",
                "MODEL_ID",
                "BASE_URL",
                "API_KEY",
                "PROVIDER",
                "API_TYPE",
                "CONTEXT_WINDOW",
                "MAX_TOKENS",
                "REASONING",
            )
        }
        entry = _entry_from_env(env, key=raw_key)
        if entry is None:
            print(
                f"[WARN] PI_MODELS 中的 {raw_key} 未配置完整"
                f"（需要 {prefix}MODEL_ID / {prefix}BASE_URL），已跳过。"
            )
            continue
        signature = (entry["base_url"], entry["id"])
        if signature in seen:
            continue
        seen.add(signature)
        models.append(entry)
    return models


def load_models() -> list[dict]:
    """解析 .env 中的模型列表。

    返回按配置顺序排列的模型字典列表，每项包含：
    ``key / label / id / name / provider / base_url / api / api_key /
    context_window / max_tokens / reasoning``。

    支持 ``PI_LLM_<GROUP>_*`` 命名分组、旧版单模型 ``PI_LLM_*``，
    以及显式列表 ``PI_MODELS`` + ``PI_MODEL_<KEY>_*``。
    """
    _load_dotenv_once()

    models: list[dict] = []
    seen: set[tuple[str, str]] = set()

    # 1) 显式列表（顺序由用户决定）
    for entry in _entry_from_explicit_list(seen):
        models.append(entry)

    # 2) 环境变量里的 PI_LLM_* 分组（默认组 "" 排最前，其余按名字排序）
    groups = _scan_llm_groups()
    global_env = groups.get("", {})

    order = [g for g in groups if g == ""] + sorted(g for g in groups if g != "")
    for group in order:
        env = groups[group]
        key = group or "default"
        entry = _entry_from_env(env, key=key, global_env=global_env)
        if entry is None:
            if group:
                print(
                    f"[WARN] 模型组 {group.upper()} 未配置完整"
                    f"（需要 PI_LLM_{group.upper()}_MODEL_ID / _BASE_URL），已跳过。"
                )
            continue
        signature = (entry["base_url"], entry["id"])
        if signature in seen:
            continue
        seen.add(signature)
        models.append(entry)

    if not models:
        print("[ERROR] 未配置任何模型！")
        print(
            "[HINT] 在 .env 中设置 PI_LLM_BASE_URL / PI_LLM_MODEL_ID / PI_LLM_API_KEY，"
            "或用 PI_LLM_<组名>_BASE_URL 这类写法追加更多模型。"
        )
        raise ValueError("No model configured. See .env.example")

    # 默认模型：PI_DEFAULT_MODEL 优先（大小写不敏感的键名），否则第一个
    default_key = (os.getenv("PI_DEFAULT_MODEL", "") or "").strip().lower()
    if default_key:
        models.sort(key=lambda m: 0 if m["key"].lower() == default_key else 1)

    return models


def load_env_config():
    """旧接口：返回 (api_key, model_config_dict)，兼容仅用单模型的调用方。"""
    models = load_models()
    first = models[0]
    return first["api_key"], dict(
        id=first["id"],
        name=first["name"],
        api=first["api"],
        provider=first["provider"],
        base_url=first["base_url"],
        input=["text"],
        context_window=first["context_window"],
        max_tokens=first["max_tokens"],
        reasoning=first["reasoning"],
    )