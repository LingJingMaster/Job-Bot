"""材料工作台页面入口。界面在 app/views/materials.py。"""

from pathlib import Path
import runpy

runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "views" / "materials.py"),
    run_name="__main__",
)
