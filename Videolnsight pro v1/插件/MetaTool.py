# -*- coding: utf-8 -*-
"""
视频元数据工具（编辑 + 一键清理 + 批量处理）
- 编辑：仅文件名 / 标题(title) / 副标题(comment) / 语言(language)，不处理字幕
- 清理：尽量清空容器与常见流级元数据标签（保留音视频流本身），不重编码
- 批量：对所选或多选文件统一处理
"""

from __future__ import annotations
PLUGIN_NAME = "视频元数据工具"

import os, shutil, subprocess
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QProgressBar,
    QPushButton, QFileDialog, QMessageBox, QComboBox, QHBoxLayout,
    QListWidget, QListWidgetItem, QLabel, QToolBar
)
from PyQt6.QtGui import QAction  # QAction 在 QtGui

# ---------- 通用 ----------
def _safe_unique_path(dirpath: str, filename: str) -> str:
    target = os.path.join(dirpath, filename)
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(filename)
    k = 1
    while True:
        alt = os.path.join(dirpath, f"{stem}({k}){ext}")
        if not os.path.exists(alt):
            return alt
        k += 1

def _run_ffmpeg(cmd: list[str], timeout_sec=900) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if p.returncode == 0:
            return True, p.stderr or p.stdout or ""
        return False, (p.stderr or p.stdout or "ffmpeg 执行失败")
    except Exception as e:
        return False, str(e)

# ---------- 主对话框：编辑 + 清理 + 批量 ----------
class MetadataToolDialog(QDialog):
    """
    - 支持批量：列表中所有文件都会处理
    - “应用编辑” ：仅写入 title/comment/language（容器级）并尝试写入第一个音频流语言；可重命名
    - “一键清理” ：清空容器元数据与常见流级标签；不改文件名
    - 均为 -c copy，避免重编码；不处理字幕内容
    """
    def __init__(self, app_main_window):
        super().__init__(app_main_window)
        self.viewer = app_main_window
        self.setWindowTitle("🎛️ 视频元数据工具（编辑 / 清理 / 批量）")
        self.setMinimumWidth(760)

        lay = QVBoxLayout(self)

        # 文件列表（自动带入主界面所选）
        self.files: list[str] = []
        lay.addWidget(QLabel("待处理文件："))
        self.listw = QListWidget()
        lay.addWidget(self.listw)

        # 自动从主界面选择带入
        try:
            sel = self.viewer.file_list_widget.selectedItems()
            if sel:
                for it in sel:
                    p = it.data(Qt.ItemDataRole.UserRole)
                    if p and p not in self.files:
                        self.files.append(p)
                        self.listw.addItem(p)
        except Exception:
            pass

        # 添加/移除按钮
        row = QHBoxLayout()
        btn_add_files = QPushButton("➕ 添加文件")
        btn_add_files.clicked.connect(self._add_files)
        btn_remove = QPushButton("➖ 移除选中")
        btn_remove.clicked.connect(self._remove_selected)
        btn_clear = QPushButton("🗑️ 清空列表")
        btn_clear.clicked.connect(self._clear_all)
        row.addWidget(btn_add_files)
        row.addWidget(btn_remove)
        row.addWidget(btn_clear)
        row.addStretch()
        lay.addLayout(row)

        # 参数区：编辑模式字段
        form = QFormLayout()
        self.edit_newname = QLineEdit(); self.edit_newname.setPlaceholderText("留空不改名（自动带扩展名）")
        self.edit_title = QLineEdit()
        self.edit_subtitle = QLineEdit()
        self.combo_lang = QComboBox(); self.combo_lang.setEditable(True)
        self.combo_lang.addItems(["und","eng","chi","zho","jpn","kor","fre","ger","spa","rus"])
        form.addRow("新文件名：", self.edit_newname)
        form.addRow("视频标题（title）：", self.edit_title)
        form.addRow("副标题（comment）：", self.edit_subtitle)
        form.addRow("语言（language）：", self.combo_lang)
        lay.addLayout(form)

        # 进度条与动作按钮
        self.progress = QProgressBar(); self.progress.setVisible(False); lay.addWidget(self.progress)

        btns = QHBoxLayout()
        self.btn_apply = QPushButton("🚀 应用编辑（批量）")
        self.btn_apply.clicked.connect(self._apply_edit_batch)
        self.btn_clear_meta = QPushButton("🧹 一键清理元数据（批量）")
        self.btn_clear_meta.clicked.connect(self._clear_meta_batch)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(self.btn_apply)
        btns.addWidget(self.btn_clear_meta)
        btns.addStretch()
        btns.addWidget(btn_close)
        lay.addLayout(btns)

    # ---------- 文件列表操作 ----------
    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", "",
            "视频文件 (*.mp4 *.mkv *.mov *.avi *.flv *.webm *.m4v *.ts *.mts *.m2ts *.3gp *.ogv);;所有文件 (*)"
        )
        for p in paths:
            if p and p not in self.files:
                self.files.append(p)
                self.listw.addItem(p)

    def _remove_selected(self):
        for it in self.listw.selectedItems():
            row = self.listw.row(it)
            self.listw.takeItem(row)
            if 0 <= row < len(self.files):
                self.files.pop(row)

    def _clear_all(self):
        self.files.clear()
        self.listw.clear()

    # ---------- 批量：编辑 ----------
    def _apply_edit_batch(self):
        if not self.files:
            QMessageBox.information(self, "提示", "请先添加需要处理的文件。")
            return

        title = self.edit_title.text().strip()
        subtitle = self.edit_subtitle.text().strip()
        newname = self.edit_newname.text().strip()
        lang = self.combo_lang.currentText().strip() or "und"

        self.progress.setVisible(True); self.progress.setRange(0, len(self.files))
        ok = 0
        for i, src in enumerate(self.files):
            try:
                self._edit_single(src, title, subtitle, lang, newname)
                ok += 1
            except Exception as e:
                QMessageBox.warning(self, "处理失败", f"{os.path.basename(src)}\n{e}")
            finally:
                self.progress.setValue(i + 1)

        self.progress.setVisible(False)
        QMessageBox.information(self, "完成", f"编辑完成：{ok}/{len(self.files)}")

    def _edit_single(self, src: str, title: str, subtitle: str, lang: str, newname: str):
        dirpath = os.path.dirname(src)
        ext = Path(src).suffix
        out_name = (newname if newname else Path(src).stem) + ext
        out_tmp = os.path.join(dirpath, f".tmp_{out_name}")
        out_path = _safe_unique_path(dirpath, out_name)

        cmd = ["ffmpeg", "-y", "-nostdin", "-i", src]
        if title:
            cmd += ["-metadata", f"title={title}"]
        if subtitle:
            cmd += ["-metadata", f"comment={subtitle}"]
        if lang:
            cmd += ["-metadata", f"language={lang}"]
        # 同时尝试写入第一个音频流语言
        cmd += ["-metadata:s:a:0", f"language={lang}"]
        cmd += ["-c", "copy", out_tmp]

        ok, msg = _run_ffmpeg(cmd)
        if not ok:
            raise RuntimeError(msg)

        os.replace(out_tmp, out_path)

        # 如果用户没改名但我们产生了新文件（外部元数据不同），则回写原名覆盖
        if Path(out_path).name != Path(src).name and not newname:
            backup = src + ".backup"
            try:
                if not os.path.exists(backup):
                    shutil.copy2(src, backup)
            except Exception:
                pass
            os.replace(out_path, src)

    # ---------- 批量：一键清理 ----------
    def _clear_meta_batch(self):
        if not self.files:
            QMessageBox.information(self, "提示", "请先添加需要处理的文件。")
            return

        self.progress.setVisible(True); self.progress.setRange(0, len(self.files))
        ok = 0
        for i, src in enumerate(self.files):
            try:
                self._clear_single(src)
                ok += 1
            except Exception as e:
                QMessageBox.warning(self, "清理失败", f"{os.path.basename(src)}\n{e}")
            finally:
                self.progress.setValue(i + 1)

        self.progress.setVisible(False)
        QMessageBox.information(self, "完成", f"清理完成：{ok}/{len(self.files)}")

    def _clear_single(self, src: str):
        """
        最大化地清理元数据（不重编码）：
        - 移除容器级元数据：-map_metadata -1
        - 移除章节：-map_chapters -1
        - 尝试清空常见标签（容器级）：title / comment / description / artist / album / date / encoder / language
        - 尝试清空常见流级标签（对若干索引位：v/a/s 0..7）：title / language
        - 保留所有流：-map 0，复制：-c copy
        说明：容器/编码器差异较大，个别封装格式可能仍残留少量内部标记。
        """
        dirpath = os.path.dirname(src)
        ext = Path(src).suffix
        out_name = Path(src).stem + ext  # 清理不改名
        out_tmp = os.path.join(dirpath, f".tmp_clean_{out_name}")
        out_path = _safe_unique_path(dirpath, out_name)

        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", src,
            "-map", "0",                 # 保留所有流
            "-map_metadata", "-1",       # 清空容器级元数据
            "-map_chapters", "-1",       # 移除章节
        ]

        # 清空常见容器级标签
        for k in ["title","comment","description","artist","album","date","encoder","language"]:
            cmd += ["-metadata", f"{k}="]  # 设为空字符串 -> 移除

        # 清空常见流级标签（对前 0..7 个索引尝试；不足的索引会被忽略）
        for idx in range(8):
            cmd += ["-metadata:s:v:"+str(idx), "title="]
            cmd += ["-metadata:s:v:"+str(idx), "language="]
            cmd += ["-metadata:s:a:"+str(idx), "title="]
            cmd += ["-metadata:s:a:"+str(idx), "language="]
            cmd += ["-metadata:s:s:"+str(idx), "title="]
            cmd += ["-metadata:s:s:"+str(idx), "language="]

        cmd += ["-c", "copy", out_tmp]

        ok, msg = _run_ffmpeg(cmd)
        if not ok:
            raise RuntimeError(msg)

        os.replace(out_tmp, out_path)

        # 用清理后的内容覆盖原文件（保留原文件名）
        if Path(out_path).name != Path(src).name:
            # 理论上 out_path 与 src 名字相同，但为了稳妥仍做覆盖逻辑
            pass
        backup = src + ".backup"
        try:
            if not os.path.exists(backup):
                shutil.copy2(src, backup)
        except Exception:
            pass
        os.replace(out_path, src)

# ---------- 插件入口/退出 ----------
def register(app):
    act_tool = QAction("🎛️ 视频元数据工具", app)
    act_tool.triggered.connect(lambda: MetadataToolDialog(app).exec())

    app.addAction(act_tool)

    bars = app.findChildren(QToolBar)
    if bars:
        tb = bars[0]
    else:
        tb = QToolBar(); app.addToolBar(tb)
    tb.addSeparator(); tb.addAction(act_tool)

    if hasattr(app, "status_bar"):
        app.status_bar.showMessage("视频元数据工具已加载：编辑 / 一键清理 / 批量处理", 4000)

    return {"actions": [act_tool], "toolbar": tb}

def unregister(app, handle):
    try:
        actions = handle.get("actions", []); tb = handle.get("toolbar")
        if tb:
            for act in actions:
                if act in tb.actions():
                    tb.removeAction(act)
        for act in actions:
            app.removeAction(act)
    except Exception:
        pass
