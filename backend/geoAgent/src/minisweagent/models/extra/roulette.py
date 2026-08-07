import random

from pydantic import BaseModel

from minisweagent import Model
from minisweagent.models import get_model


class RouletteModelConfig(BaseModel):
    model_kwargs: list[dict]
    """可供选择的模型"""
    model_name: str = "roulette"


class RouletteModel:
    def __init__(self, *, config_class: type = RouletteModelConfig, **kwargs):
        """这个 "meta"-模型在每次调用时随机选择一个模型。"""
        self.config = config_class(**kwargs)
        self.models = [get_model(config=config) for config in self.config.model_kwargs]
        self._n_calls = 0

    def get_template_vars(self, **kwargs) -> dict:
        return self.config.model_dump()

    def select_model(self) -> Model:
        return random.choice(self.models)

    def query(self, *args, **kwargs) -> dict:
        model = self.select_model()
        self._n_calls += 1
        response = model.query(*args, **kwargs)
        response["model_name"] = model.config.model_name
        return response

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "model": self.config.model_dump(mode="json"),
                    "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
            }
        }


class InterleavingModelConfig(BaseModel):
    model_kwargs: list[dict]
    sequence: list[int] | None = None
    """如果设置为 0, 0, 1，我们将返回第一个模型 2 次，然后是第二个模型 1 次，
    然后又是第一个模型，依此类推。"""
    model_name: str = "interleaving"


class InterleavingModel(RouletteModel):
    def __init__(self, *, config_class: type = InterleavingModelConfig, **kwargs):
        """这个 "meta"-模型按顺序在每次调用时在模型之间交替。"""
        super().__init__(config_class=config_class, **kwargs)

    def select_model(self) -> Model:
        if self.config.sequence is None:
            i_model = self._n_calls % len(self.models)
        else:
            i_model = self.config.sequence[self._n_calls % len(self.config.sequence)]
        return self.models[i_model]
