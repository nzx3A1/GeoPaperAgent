class InterruptAgentFlow(Exception):
    """抛出以中断代理流程并添加消息。"""

    def __init__(self, *messages: dict):
        self.messages = messages
        super().__init__()


class Submitted(InterruptAgentFlow):
    """当代理已完成其任务时抛出。"""


class LimitsExceeded(InterruptAgentFlow):
    """当代理超出成本或步数限制时抛出。"""


class TimeExceeded(LimitsExceeded):
    """当代理超出挂钟时间限制时抛出。"""


class UserInterruption(InterruptAgentFlow):
    """当用户中断代理时抛出。"""


class FormatError(InterruptAgentFlow):
    """当语言模型的输出不符合预期格式时抛出。"""
