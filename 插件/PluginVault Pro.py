# -*- coding: utf-8 -*-
"""
插件中心（Plugin Hub）
- 新增：从网络URL安装插件（支持直接下载.py文件）
- 其他功能保持不变...
"""

import os, sys, shutil, zipfile, time, re, traceback, subprocess, requests  # 新增requests导入
from pathlib import Path

from PyQt6.QtCore import Qt, QMimeData, QPoint, QEvent
from PyQt6.QtGui  import QAction, QCursor, QPainter, QPen, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QMessageBox, QHeaderView,
    QFileDialog, QFrame, QMenu, QStyledItemDelegate, QStyleOptionViewItem, QStyle,
    QInputDialog  # 新增导入
)

PLUGIN_NAME = "插件中心"
AUTHOR = "顾念迟"
PLUGIN_USAGE = "插件中心"

# ---------- 工具函数 ----------
# （原有工具函数保持不变，此处省略，实际使用时需保留）
def _read_plugin_name_from_file(py_path: str) -> str:
    """更稳的 PLUGIN_NAME 解析，失败用文件名"""
    try:
        with open(py_path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(120):
                line = f.readline()
                if not line:
                    break
                m = re.search(r"""^\s*PLUGIN_NAME\s*=\s*['"](.+?)['"]""", line)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    return Path(py_path).stem

def _read_plugin_comment(py_path: str) -> str:
    """自动识别“插件注释”"""
    try:
        with open(py_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(4096)
        m = re.search(r'^\s*(?:[ruRUfF]{0,2})("""|\'\'\')(.*?)(\1)', content, re.S)
        if m:
            text = m.group(2)
        else:
            text = []
            for line in content.splitlines():
                if re.match(r'^\s*#', line):
                    text.append(re.sub(r'^\s*#\s?', '', line).rstrip())
                elif re.match(r'^\s*$', line):
                    if text:
                        text.append("")
                else:
                    break
            text = "\n".join(text).strip()
        text = re.sub(r'\n{3,}', '\n\n', (text or '').strip())
        return text if text else "（未检测到注释）"
    except Exception:
        return "（未检测到注释）"

def _safe_move_to_removed(src: str, plugins_dir: str) -> str:
    removed_dir = os.path.join(plugins_dir, "__removed__")
    os.makedirs(removed_dir, exist_ok=True)
    base = os.path.basename(src)
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(removed_dir, f"{ts}_{base}")
    shutil.move(src, dst)
    return dst

def _copy_or_overwrite(src: str, dst_dir: str) -> str:
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.abspath(src) == os.path.abspath(dst):
        return dst
    if os.path.exists(dst):
        _safe_move_to_removed(dst, dst_dir)
    shutil.copy2(src, dst)
    return dst

def _extract_zip_py(zip_path: str, dst_dir: str) -> list[str]:
    out = []
    with zipfile.ZipFile(zip_path, "r") as z:
        for name in z.namelist():
            if name.lower().endswith(".py") and not name.endswith("/"):
                data = z.read(name)
                leaf = os.path.basename(name)
                dst = os.path.join(dst_dir, leaf)
                if os.path.exists(dst):
                    _safe_move_to_removed(dst, dst_dir)
                with open(dst, "wb") as f:
                    f.write(data)
                out.append(dst)
    return out

def _locate_file_in_explorer(file_path: str) -> bool:
    """跨平台定位文件"""
    if not os.path.exists(file_path):
        return False
    try:
        if sys.platform.startswith('win32'):
            subprocess.Popen(f'explorer.exe /select,"{file_path}"')
        elif sys.platform.startswith('darwin'):
            subprocess.Popen(['open', '-R', file_path])
        else:
            dir_path = os.path.dirname(file_path)
            subprocess.Popen(['xdg-open', dir_path])
        return True
    except Exception:
        traceback.print_exc()
        return False


# ---------- 自定义：选中描边委托 ----------
class SelectBorderDelegate(QStyledItemDelegate):
    def __init__(self, table, border_color="#4a90e2", radius=8, width=2):
        super().__init__(table)
        self.table = table
        self._color = QColor(border_color)
        self._radius = radius
        self._width = width

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        opt = QStyleOptionViewItem(option)
        if opt.state & QStyle.StateFlag.State_Selected:
            opt.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, opt, index)

        selection_model = self.table.selectionModel()
        if not selection_model:
            return

        row = index.row()
        if self.table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows:
            selected_rows = {i.row() for i in selection_model.selectedRows()}
        else:
            selected_rows = {i.row() for i in selection_model.selectedIndexes()}

        if row in selected_rows:
            row_rect = self.table.visualRect(self.table.model().index(row, 0))
            cols = self.table.model().columnCount()
            for c in range(1, cols):
                row_rect = row_rect.united(self.table.visualRect(self.table.model().index(row, c)))

            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(self._color)
            pen.setWidth(self._width)
            painter.setPen(pen)
            rect = row_rect.adjusted(2, 2, -2, -2)
            painter.drawRoundedRect(rect, self._radius, self._radius)
            painter.restore()


# ---------- 主窗口 ----------
class PluginHubDialog(QDialog):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.setWindowTitle(f"插件中心 - 作者：{AUTHOR}")
        self.setMinimumSize(960, 650)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

        self.plugin_desc_cache: dict[str, str] = {}
        self._current_hover_row = -1

        lay = QVBoxLayout(self)

        # 悬停浮层
        self.info_overlay = QLabel(self)
        self.info_overlay.setWordWrap(True)
        self.info_overlay.setStyleSheet("""
            QLabel {
                color: #FFFFFF;
                background-color: rgba(0, 0, 0, .6);
                border-radius: 10px;
                padding: 12px 14px;
                font-size: 13px;
                max-width: 520px;
            }
        """)
        self.info_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._overlay_margin = 10
        self._overlay_default_text = f"作者：{AUTHOR}\n{PLUGIN_USAGE}"
        self.info_overlay.setText(self._overlay_default_text)
        self.info_overlay.adjustSize()
        self.info_overlay.hide()

        # 顶部搜索栏（新增“从网络安装”按钮）
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索插件名称… 支持拖拽/.py/.zip/网络URL安装")
        btn_refresh = QPushButton("刷新")
        btn_install = QPushButton("本地安装…")  # 重命名更清晰
        btn_net_install = QPushButton("从网络安装…")  # 新增网络安装按钮
        btn_refresh.clicked.connect(self.refresh)
        btn_install.clicked.connect(self.install_from_file)
        btn_net_install.clicked.connect(self.install_from_url)  # 绑定网络安装方法
        top.addWidget(QLabel("插件："))
        top.addWidget(self.search, 1)
        top.addWidget(btn_refresh)
        top.addWidget(btn_install)
        top.addWidget(btn_net_install)  # 添加到布局
        lay.addLayout(top)

        # 插件表格（保持不变）
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["启用", "名称", "路径"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(150)
        self.table.setColumnWidth(1, 220)
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        lay.addWidget(self.table, 1)

        # 表格事件监听（保持不变）
        self.table.viewport().installEventFilter(self)
        self.table.viewport().leaveEvent = self._on_table_leave

        # 底部操作栏（保持不变）
        bottom = QHBoxLayout()
        self.btn_enable = QPushButton("启用选中")
        self.btn_disable = QPushButton("禁用选中")
        bottom.addStretch()
        bottom.addWidget(self.btn_enable)
        bottom.addWidget(self.btn_disable)
        lay.addLayout(bottom)

        # 绑定事件（保持不变）
        self.btn_enable.clicked.connect(lambda: self.bulk_set_enabled(True))
        self.btn_disable.clicked.connect(lambda: self.bulk_set_enabled(False))
        self.search.textChanged.connect(self._apply_filter)

        # 样式适配（保持不变）
        self._apply_indicator_style()
        is_dark = getattr(self.viewer, "theme", "dark") == "dark"
        border_color = "#3a7bcd" if is_dark else "#4a90e2"
        self.table.setItemDelegate(SelectBorderDelegate(self.table, border_color=border_color))

        self.refresh()

    # ---------- 新增：从网络URL安装插件 ----------
    def install_from_url(self):
        """从网络URL安装插件（支持直接下载.py文件）"""
        # 弹出输入框，让用户输入插件URL
        url, ok = QInputDialog.getText(
            self,
            "从网络安装插件",
            "请输入插件文件的直接下载URL（.py格式）："
        )
        if not ok or not url.strip():
            return
        url = url.strip()

        # 验证URL是否以.py结尾（基本过滤）
        if not url.lower().endswith(".py"):
            QMessageBox.warning(
                self,
                "URL无效",
                "请输入以.py结尾的插件文件URL（仅支持单个Python文件）"
            )
            return

        try:
            # 显示下载中状态
            self.show_status_message("正在从网络下载插件...")

            # 发送网络请求下载文件（设置超时10秒）
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()  # 抛出HTTP错误（如404、500）

            # 从URL提取文件名（优先用URL中的文件名，否则生成随机名）
            filename = os.path.basename(url.split("?")[0])  # 去除URL参数
            if not filename.endswith(".py"):
                filename = f"net_installed_plugin_{int(time.time())}.py"

            # 保存文件到插件目录
            plugin_path = os.path.join(self.viewer.plugins_dir, filename)
            with open(plugin_path, "wb") as f:
                f.write(response.content)

            # 检查文件有效性（避免空文件）
            if os.path.getsize(plugin_path) < 10:
                os.remove(plugin_path)
                QMessageBox.warning(self, "下载失败", "获取的文件为空，可能URL无效或文件已被删除")
                return

            # 安装后处理（与本地安装逻辑一致）
            name = _read_plugin_name_from_file(plugin_path)
            self.viewer.plugins_found[name] = {"path": plugin_path}

            # 自动启用新插件
            enabled = self.viewer._read_enabled_plugins()
            if name not in enabled:
                enabled.add(name)
                self.viewer._write_enabled_plugins(enabled)

            # 加载插件
            self.viewer.load_plugin(name)
            self.refresh()

            QMessageBox.information(
                self,
                "安装成功",
                f"已从网络安装插件：\n名称：{name}\n文件路径：{plugin_path}"
            )

        except requests.exceptions.HTTPError as e:
            QMessageBox.critical(self, "HTTP错误", f"下载失败：服务器返回错误\n{str(e)}")
        except requests.exceptions.Timeout:
            QMessageBox.critical(self, "超时错误", "下载超时，请检查网络连接或稍后重试")
        except requests.exceptions.ConnectionError:
            QMessageBox.critical(self, "连接错误", "无法连接到服务器，请检查网络连接")
        except Exception as e:
            traceback.print_exc()
            QMessageBox.critical(self, "安装失败", f"处理文件时出错：\n{str(e)}")
        finally:
            self.clear_status_message()

    # ---------- 新增：状态消息辅助方法 ----------
    def show_status_message(self, message):
        if hasattr(self.viewer, 'status_bar') and self.viewer.status_bar.isVisible():
            self.viewer.status_bar.showMessage(message)

    def clear_status_message(self):
        if hasattr(self.viewer, 'status_bar') and self.viewer.status_bar.isVisible():
            self.viewer.status_bar.clearMessage()

    # ---------- 原有方法保持不变（以下代码仅为示意，实际使用时需保留完整实现） ----------
    def enterEvent(self, e):
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.info_overlay.hide()
        self._current_hover_row = -1
        super().leaveEvent(e)

    def resizeEvent(self, e):
        self._position_overlay()
        super().resizeEvent(e)

    def _position_overlay(self, near_cursor: bool = False):
        if near_cursor:
            p = self.mapFromGlobal(QCursor.pos())
            x = max(self._overlay_margin, min(p.x() + 16, self.width() - self.info_overlay.width() - self._overlay_margin))
            y = max(self._overlay_margin, min(p.y() + 16, self.height() - self.info_overlay.height() - self._overlay_margin))
        else:
            x = self._overlay_margin
            y = self._overlay_margin
        self.info_overlay.move(x, y)
        self.info_overlay.adjustSize()

    def _on_table_leave(self, event):
        self.info_overlay.hide()
        self._current_hover_row = -1
        if hasattr(super(type(self.table.viewport()), self.table.viewport()), 'leaveEvent'):
            super(type(self.table.viewport()), self.table.viewport()).leaveEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.table.viewport():
            if event.type() == QEvent.Type.MouseMove:
                index = self.table.indexAt(event.pos())
                if index.isValid() and index.row() != self._current_hover_row:
                    self._current_hover_row = index.row()
                    row = index.row()
                    name = self.table.item(row, 1).text()
                    path = self.table.item(row, 2).text()
                    desc = self.plugin_desc_cache.get(path)
                    if desc is None:
                        desc = _read_plugin_comment(path) if path and os.path.exists(path) else "（未检测到注释）"
                        self.plugin_desc_cache[path] = desc
                    text = f"插件：{name}\n注释：{desc}"
                    self.info_overlay.setText(text)
                    self.info_overlay.adjustSize()
                    self._position_overlay(near_cursor=True)
                    self.info_overlay.show()
                elif not index.isValid() and self._current_hover_row != -1:
                    self.info_overlay.hide()
                    self._current_hover_row = -1
        return super().eventFilter(obj, event)

    def show_context_menu(self, position):
        selected_rows = set(i.row() for i in self.table.selectedItems())
        if not selected_rows:
            return
        menu = QMenu()
        locate_action = QAction("定位文件", self)
        locate_action.triggered.connect(self.locate_selected_files)
        menu.addAction(locate_action)
        menu.addSeparator()
        remove_action = QAction("移除选中（安全移动）", self)
        remove_action.triggered.connect(self.bulk_remove)
        menu.addAction(remove_action)
        menu.exec(QCursor.pos())

    def _apply_indicator_style(self):
        is_dark = getattr(self.viewer, "theme", "dark") == "dark"
        border = "#FFFFFF" if is_dark else "#000000"
        fill   = "#FFFFFF" if is_dark else "#000000"
        hover  = "rgba(255,255,255,0.08)" if is_dark else "rgba(0,0,0,0.06)"

        self.table.setStyleSheet(self.table.styleSheet() + f"""
QTableView {{
    border-radius: 8px;
    padding: 5px;
    outline: 0;
    selection-background-color: transparent;
    selection-color: {'#FFFFFF' if is_dark else '#000000'};
}}
QTableView::item {{
    padding: 12px 15px;
    margin: 4px 8px;
    border-radius: 8px;
    color: {'#FFFFFF' if is_dark else '#000000'};
    font-size: 13px;
}}
QTableView::item:hover {{
    background: {hover};
}}
QTableView::item:selected {{
    background: transparent;
    color: {'#FFFFFF' if is_dark else '#000000'};
}}
QTableView::indicator {{
    width: 22px;
    height: 22px;
    margin: 0 10px;
}}
QTableView::indicator:unchecked {{
    border: 2px solid {border};
    background: transparent;
    border-radius: 6px;
}}
QTableView::indicator:checked {{
    border: 2px solid {border};
    background: {fill};
    image: none;
    border-radius: 6px;
}}
""")

    def refresh(self):
        self.viewer.scan_plugins()
        enabled = self.viewer._read_enabled_plugins()
        items = sorted(self.viewer.plugins_found.items(), key=lambda kv: kv[0].lower())

        self.table.setRowCount(0)
        for name, meta in items:
            path = meta.get("path", "")
            r = self.table.rowCount()
            self.table.insertRow(r)

            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            chk.setCheckState(Qt.CheckState.Checked if name in enabled else Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, name)
            self.table.setItem(r, 0, chk)

            it_name = QTableWidgetItem(name)
            it_name.setToolTip(name)
            it_name.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, 1, it_name)

            it_path = QTableWidgetItem(path)
            it_path.setToolTip(path)
            it_path.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, 2, it_path)

        self.plugin_desc_cache.clear()
        self._apply_filter(self.search.text())

    def _apply_filter(self, text: str):
        text = (text or "").strip().lower()
        for r in range(self.table.rowCount()):
            name = self.table.item(r, 1).text().lower()
            path = self.table.item(r, 2).text().lower()
            visible = (text in name) or (text in path)
            self.table.setRowHidden(r, not visible)

    def bulk_set_enabled(self, on: bool):
        names = []
        for r in range(self.table.rowCount()):
            if self.table.isRowHidden(r):
                continue
            if self.table.item(r, 1).isSelected() or self.table.item(r, 0).isSelected():
                chk = self.table.item(r, 0)
                chk.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
                names.append(chk.data(Qt.ItemDataRole.UserRole))
        current = self.viewer._read_enabled_plugins()
        changed = False
        for n in names:
            if on and n not in current:
                current.add(n); changed = True
            if (not on) and n in current:
                current.remove(n); changed = True
        if changed:
            self.viewer._write_enabled_plugins(current)
            if on:
                for n in names:
                    if n in self.viewer.plugins_found:
                        self.viewer.load_plugin(n)
            else:
                for n in names:
                    if n in self.viewer.plugins_loaded:
                        self.viewer.unload_plugin(n)
            if hasattr(self.viewer, 'status_bar') and self.viewer.status_bar.isVisible():
                self.viewer.status_bar.showMessage(("已启用" if on else "已禁用") + f" {len(names)} 个插件", 3000)

    def bulk_remove(self):
        rows = [r for r in range(self.table.rowCount()) if self.table.item(r,1).isSelected() or self.table.item(r,0).isSelected()]
        if not rows:
            QMessageBox.information(self, "提示", "请先选中要移除的插件行")
            return
        if QMessageBox.question(self, "确认移除", "将把所选插件文件**移动**到 plugins/__removed__/ 下，确认？") != QMessageBox.StandardButton.Yes:
            return

        names_to_remove = []
        for r in rows:
            name = self.table.item(r, 1).text()
            path = self.table.item(r, 2).text()
            try:
                if name in self.viewer.plugins_loaded:
                    self.viewer.unload_plugin(name)
                enabled = self.viewer._read_enabled_plugins()
                if name in enabled:
                    enabled.remove(name)
                    self.viewer._write_enabled_plugins(enabled)
                if os.path.exists(path):
                    _safe_move_to_removed(path, self.viewer.plugins_dir)
                names_to_remove.append(name)
            except Exception as e:
                traceback.print_exc()
                QMessageBox.critical(self, "移除失败", f"{name}\n{e}")

        self.refresh()
        if hasattr(self.viewer, 'status_bar') and self.viewer.status_bar.isVisible():
            self.viewer.status_bar.showMessage(f"已移除 {len(names_to_remove)} 个插件（可在 __removed__ 恢复）", 4000)

    def locate_selected_files(self):
        selected_rows = [r for r in range(self.table.rowCount())
                        if (self.table.item(r,1).isSelected() or self.table.item(r,0).isSelected())
                        and not self.table.isRowHidden(r)]
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先选中要定位的插件行")
            return

        fail_count = 0
        for r in selected_rows:
            file_path = self.table.item(r, 2).text()
            if not file_path or not os.path.exists(file_path) or not _locate_file_in_explorer(file_path):
                fail_count += 1
            else:
                time.sleep(0.3)

        if fail_count > 0:
            QMessageBox.warning(self, "定位失败", f"{fail_count} 个插件文件不存在或定位失败")

    def install_from_file(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择要安装的插件文件", "", "Python (*.py);;ZIP (*.zip);;所有文件 (*)")
        if not files:
            return
        self._install_files(files)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if not e.mimeData().hasUrls():
            return
        files = []
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p and os.path.exists(p):
                files.append(p)
        if files:
            self._install_files(files)
            e.acceptProposedAction()

    def _install_files(self, files: list[str]):
        installed = []
        for p in files:
            try:
                if p.lower().endswith(".py"):
                    dst = _copy_or_overwrite(p, self.viewer.plugins_dir)
                    installed.append(dst)
                elif p.lower().endswith(".zip"):
                    out = _extract_zip_py(p, self.viewer.plugins_dir)
                    installed.extend(out)
                else:
                    continue
            except Exception as e:
                traceback.print_exc()
                QMessageBox.critical(self, "安装失败", f"{os.path.basename(p)}\n{e}")
        if installed:
            enabled = self.viewer._read_enabled_plugins()
            changed = False
            for py in installed:
                name = _read_plugin_name_from_file(py)
                self.viewer.plugins_found[name] = {"path": py}
                if name not in enabled:
                    enabled.add(name); changed = True
            if changed:
                self.viewer._write_enabled_plugins(enabled)
            for py in installed:
                name = _read_plugin_name_from_file(py)
                self.viewer.load_plugin(name)
            self.refresh()
            QMessageBox.information(self, "安装成功", f"已安装并启用 {len(installed)} 个插件。")
        else:
            QMessageBox.information(self, "提示", "未找到可安装的 .py 文件。")

# ---------- 插件对接 ----------
def register(app):
    menu = getattr(app, "plugins_menu", None)
    act = QAction("插件中心", app)
    act.triggered.connect(lambda: PluginHubDialog(app).exec())
    if menu:
        menu.insertAction(menu.actions()[0] if menu.actions() else None, act)
        menu.insertSeparator(menu.actions()[1] if len(menu.actions()) > 1 else None)
    return {"action": act}

def unregister(app, handle):
    try:
        act = handle.get("action")
        if act:
            act.deleteLater()
    except Exception:
        pass