"""Boss 只读采集与手动导入。

浏览器只在 runner 真正启动时才会加载。本包导入本身不会打开浏览器。
"""

from jobbot.collector.detail_parser import parse_detail
from jobbot.collector.manual_import import import_manual
from jobbot.collector.runs import start_run, stop_run

__all__ = ["parse_detail", "import_manual", "start_run", "stop_run"]
