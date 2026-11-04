"""Streamlit 入口。只应通过 Makefile 绑定 127.0.0.1。"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Job-Bot", layout="wide")
st.navigation(
    [
        st.Page("views/settings.py", title="设置与状态", default=True),
        st.Page("views/resume.py", title="简历与事实"),
        st.Page("views/collect.py", title="岗位采集"),
        st.Page("views/match.py", title="岗位与匹配"),
        st.Page("views/materials.py", title="材料工作台"),
        st.Page("views/tracking.py", title="申请跟踪"),
        st.Page("views/labels.py", title="标注与实验"),
    ]
).run()
