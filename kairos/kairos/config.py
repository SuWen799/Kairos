"""Kairos 配置加载。优先 config.local.json（不入 git），缺省回落环境变量。"""
import json
import os
import sys

# 打包态（PyInstaller）：BASE_DIR 取 exe 所在目录（resources/backend/），
# 只读安装目录不落数据——数据目录一律走 KAIROS_DATA_DIR（Electron 注入 userData/data）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("KAIROS_DATA_DIR") or os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)


def _local_config_path() -> str:
    """config.local.json 权威路径：DATA_DIR 优先；开发态老位置存在则沿用（兼容）。"""
    p = os.path.join(DATA_DIR, "config.local.json")
    if not os.path.exists(p):
        legacy = os.path.join(BASE_DIR, "config.local.json")
        if os.path.exists(legacy):
            return legacy
    return p


LOCAL_CONFIG_PATH = _local_config_path()


def _load():
    path = LOCAL_CONFIG_PATH
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "llm": {
            "base_url": os.environ.get("KAIROS_LLM_BASE_URL", "https://api.deepseek.com/v1"),
            "api_key": os.environ.get("KAIROS_LLM_API_KEY", ""),
            "model": os.environ.get("KAIROS_LLM_MODEL", "deepseek-chat"),
        },
        "server": {
            "host": os.environ.get("KAIROS_HOST", "127.0.0.1"),
            "port": int(os.environ.get("KAIROS_PORT", "8626")),
        },
    }


CFG = _load()

# 环境变量优先级最高（Electron spawnSidecar 注入动态端口用），覆盖 config.local.json
if os.environ.get("KAIROS_HOST"):
    CFG["server"]["host"] = os.environ["KAIROS_HOST"]
if os.environ.get("KAIROS_PORT"):
    CFG["server"]["port"] = int(os.environ["KAIROS_PORT"])
if os.environ.get("KAIROS_LLM_BASE_URL"):
    CFG["llm"]["base_url"] = os.environ["KAIROS_LLM_BASE_URL"]
if os.environ.get("KAIROS_LLM_MODEL"):
    CFG["llm"]["model"] = os.environ["KAIROS_LLM_MODEL"]

# 凭据层（需求 9.1）：keyring 优先，config.local.json 明文仅兜底（迁移脚本置空后即失效）
try:
    from .credentials import get_secret
    _kr = get_secret("llm.api_key")
    if _kr:
        CFG["llm"]["api_key"] = _kr
except Exception:
    pass

LLM = CFG["llm"]
SERVER = CFG["server"]


def _norm_models() -> list[dict]:
    """多模型注册表（需求 v2.0 R4）：config.local.json 的 models 数组，每项可独立
    base_url/api_key/model/label/tier。缺省回落单一 llm 配置。"""
    out, seen = [], set()
    for m in CFG.get("models", []):
        name = (m.get("name") or "").strip()
        if not name or name in seen:
            continue
        out.append({
            "name": name,
            "label": m.get("label", name),
            "model": m.get("model", name),
            "base_url": m.get("base_url", LLM["base_url"]),
            "api_key": m.get("api_key", LLM["api_key"]),
            "tier": m.get("tier", "default"),  # default|reasoning|fast
        })
        seen.add(name)
    if not out:
        name = LLM.get("model", "default")
        out = [{"name": name, "label": name, "model": name,
                "base_url": LLM["base_url"], "api_key": LLM["api_key"],
                "tier": "default"}]
    return out


MODELS = _norm_models()
DEFAULT_MODEL = next(
    (m["name"] for m in MODELS if m["name"] == LLM.get("model")),
    MODELS[0]["name"])


def reload_models() -> None:
    """v2.6 配置向导：写完 config 后热重载模型注册表（不重启 sidecar）。
    注意：外部模块须以 config.MODELS 动态引用，不要 from-import 常量。"""
    global MODELS, DEFAULT_MODEL, LLM
    try:
        from .credentials import get_secret
        _kr = get_secret("llm.api_key")
        if _kr:
            CFG["llm"]["api_key"] = _kr
    except Exception:
        pass
    LLM = CFG["llm"]
    MODELS = _norm_models()
    DEFAULT_MODEL = next(
        (m["name"] for m in MODELS if m["name"] == LLM.get("model")),
        MODELS[0]["name"])

# 全局设置（v2.0 R5/R6/R7）：沙箱档 / 多 Agent 开关 / 并发上限
_DEFAULT_SETTINGS = {"sandbox": "basic", "multi_agent": True,
                     "max_subagents": 3, "default_permission": "standard"}
SETTINGS = {**_DEFAULT_SETTINGS, **CFG.get("settings", {})}


def save_settings(patch: dict) -> dict:
    """合并写回 config.local.json（保留其余键），并热更新 SETTINGS。"""
    global SETTINGS
    SETTINGS = {**SETTINGS, **patch}
    CFG["settings"] = SETTINGS
    path = LOCAL_CONFIG_PATH
    doc = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    doc["settings"] = SETTINGS
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return SETTINGS


DB_PATH = os.path.join(DATA_DIR, "kairos.db")
LEGACY_DB_PATH = os.path.join(DATA_DIR, "evogent.db")  # v0.4 旧库：旧 API 未切换前继续使用
