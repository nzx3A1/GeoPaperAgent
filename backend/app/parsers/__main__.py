"""解析器包命令行入口，默认启动 A–E 全流程。"""

from app.parsers.pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
