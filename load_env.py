import os
from pathlib import Path


# ============================================================
# 加载 .env 文件（优先），否则使用环境变量
# ============================================================

def load_env_config():
    """从 .env 文件或环境变量加载配置。"""
    # 尝试加载 .env 文件
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
            print(f"[INFO] 已加载 .env 文件: {env_path}")
        except ImportError:
            print("[WARN] python-dotenv 未安装，跳过 .env 加载。")
            print("[HINT] 运行: pip install python-dotenv")

    # 从环境变量读取配置
    api_key = os.getenv("PI_LLM_API_KEY", "")
    model_id = os.getenv("PI_LLM_MODEL_ID", "")
    model_name = os.getenv("PI_LLM_MODEL_NAME", "")
    api_type = os.getenv("PI_LLM_API_TYPE", "openai-completions")
    provider = os.getenv("PI_LLM_PROVIDER", "wqzh-local")
    base_url = os.getenv("PI_LLM_BASE_URL", "")
    context_window = int(os.getenv("PI_LLM_CONTEXT_WINDOW", "54000"))
    max_tokens = int(os.getenv("PI_LLM_MAX_TOKENS", "8192"))
    reasoning = os.getenv("PI_LLM_REASONING", "false").lower() == "true"

    # 验证必填项
    if not api_key:
        print("[ERROR] PI_LLM_API_KEY 未配置！")
        print("[HINT] 复制 .env.example 为 .env 并填入你的配置")
        raise ValueError("PI_LLM_API_KEY is required")

    return api_key, dict(
        id=model_id,
        name=model_name,
        api=api_type,
        provider=provider,
        base_url=base_url,
        input=["text"],
        context_window=context_window,
        max_tokens=max_tokens,
        reasoning=reasoning,
    )