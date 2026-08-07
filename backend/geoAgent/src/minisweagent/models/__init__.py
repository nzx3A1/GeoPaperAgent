"""此文件提供用于选择模型的便捷函数。
如果你在运行脚本中显式设置了模型，则可以完全忽略此文件。
"""

import copy
import importlib
import os
import threading

from minisweagent import Model


class GlobalModelStats:
    """全局模型统计跟踪器，带可选限制。"""

    def __init__(self):
        self._cost = 0.0
        self._n_calls = 0
        self._lock = threading.Lock()
        self.cost_limit = float(os.getenv("MSWEA_GLOBAL_COST_LIMIT", "0"))
        self.call_limit = int(os.getenv("MSWEA_GLOBAL_CALL_LIMIT", "0"))
        if (self.cost_limit > 0 or self.call_limit > 0) and not os.getenv("MSWEA_SILENT_STARTUP"):
            print(f"Global cost/call limit: ${self.cost_limit:.4f} / {self.call_limit}")

    def add(self, cost: float) -> None:
        """添加一次模型调用及其成本，并检查限制。"""
        with self._lock:
            self._cost += cost
            self._n_calls += 1
        if 0 < self.cost_limit < self._cost or 0 < self.call_limit < self._n_calls + 1:
            raise RuntimeError(f"Global cost/call limit exceeded: ${self._cost:.4f} / {self._n_calls}")

    @property
    def cost(self) -> float:
        return self._cost

    @property
    def n_calls(self) -> int:
        return self._n_calls


GLOBAL_MODEL_STATS = GlobalModelStats()


def get_model(input_model_name: str | None = None, config: dict | None = None) -> Model:
        """从任何类型的用户输入或设置中获取一个初始化好的模型对象。"""
        resolved_model_name = get_model_name(input_model_name, config)
        if config is None:
            config = {}
        config = copy.deepcopy(config)
        config["model_name"] = resolved_model_name

        model_class = get_model_class(resolved_model_name, config.pop("model_class", ""))

        if (
            any(s in resolved_model_name.lower() for s in ["anthropic", "sonnet", "opus", "claude"])
            and "set_cache_control" not in config
        ):
            # 默认情况下为 Anthropic 模型选择缓存控制
            config["set_cache_control"] = "default_end"

        return model_class(**config)


def get_model_name(input_model_name: str | None = None, config: dict | None = None) -> str:
        """从任何类型的用户输入或设置中获取模型名称。"""
        if config is None:
            config = {}
        if input_model_name:
            return input_model_name
        if from_config := config.get("model_name"):
            return from_config
        if from_env := os.getenv("MSWEA_MODEL_NAME"):
            return from_env
        raise ValueError("No default model set. Please run `mini-extra config setup` to set one.")


_MODEL_CLASS_MAPPING = {
    "litellm": "minisweagent.models.litellm_model.LitellmModel",
    "litellm_textbased": "minisweagent.models.litellm_textbased_model.LitellmTextbasedModel",
    "litellm_response": "minisweagent.models.litellm_response_model.LitellmResponseModel",
    "openrouter": "minisweagent.models.openrouter_model.OpenRouterModel",
    "openrouter_textbased": "minisweagent.models.openrouter_textbased_model.OpenRouterTextbasedModel",
    "openrouter_response": "minisweagent.models.openrouter_response_model.OpenRouterResponseModel",
    "portkey": "minisweagent.models.portkey_model.PortkeyModel",
    "portkey_response": "minisweagent.models.portkey_response_model.PortkeyResponseAPIModel",
    "requesty": "minisweagent.models.requesty_model.RequestyModel",
    "deterministic": "minisweagent.models.test_models.DeterministicModel",
}


def get_model_class(model_name: str, model_class: str = "") -> type:
        """选择最佳的模型类。

        如果提供了 model_class（作为快捷名称或完整导入路径，
        例如 "anthropic" 或 "minisweagent.models.anthropic.AnthropicModel"），
        则它优先于 `model_name`。
        否则，使用 model_name 来选择最佳的模型类。
        """
        if model_class:
            full_path = _MODEL_CLASS_MAPPING.get(model_class, model_class)
            try:
                module_name, class_name = full_path.rsplit(".", 1)
                module = importlib.import_module(module_name)
                return getattr(module, class_name)
            except (ValueError, ImportError, AttributeError):
                msg = f"Unknown model class: {model_class} (resolved to {full_path}, available: {_MODEL_CLASS_MAPPING})"
                raise ValueError(msg)

        # 默认使用 LitellmModel
        from minisweagent.models.litellm_model import LitellmModel

        return LitellmModel
