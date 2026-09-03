from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class MinerUConfig:
    """MinerU 云端接口配置。"""

    token: str = "eyJ0eXBlIjoiSldUIiwiYWxnIjoiSFM1MTIifQ.eyJqdGkiOiI1OTIwMDEwNiIsInJvbCI6IlJPTEVfUkVHSVNURVIiLCJpc3MiOiJPcGVuWExhYiIsImlhdCI6MTc4MDMwMDU5MywiY2xpZW50SWQiOiJsa3pkeDU3bnZ5MjJqa3BxOXgydyIsInBob25lIjoiMTgzNzMyOTg1NzMiLCJvcGVuSWQiOm51bGwsInV1aWQiOiI0MzU0Y2UwOS0zMzBmLTRlZTYtOWM0NS0zMzkxOGQxNWVmOWIiLCJlbWFpbCI6IiIsImV4cCI6MTc4ODA3NjU5M30.JTbfGAAmQHprsWUJ0Dk1FnlR_93_Cb1uaMe9hjrfUMDR8ou9DfbG9oCWlJN1AoanMaa0TJGqvdyZ0Pnf_0yHZw"
    batch_url: str = "https://mineru.net/api/v4/file-urls/batch"
    timeout_secs: float = 120.0
