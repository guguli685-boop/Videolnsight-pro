import importlib.util
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

import psutil
from PyQt6.QtCore import (Qt, QSize, QThread, pyqtSignal, QSettings, QTimer, QRectF)
from PyQt6.QtGui import (QPalette, QColor, QIcon, QPixmap, QPainter, QPainterPath, QAction, QGuiApplication)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton, QFileDialog, QScrollArea,
    QFrame, QMessageBox, QTabWidget, QListWidget, QListWidgetItem, QProgressBar, QSplitter, QToolBar, QStatusBar,
    QTextEdit, QMenu, QDialog, QDialogButtonBox
)

def relative_luminance(c: QColor) -> float:
    def ch(x: float) -> float:
        x = x / 255.0
        return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())

# =============================
# 应用设置
# =============================
class AppSettings:
    ORG = "dwai"
    APP = "VideoInsightPro"
    def __init__(self):
        self.qs = QSettings(self.ORG, self.APP)
    def load(self):
        return {
            "statusbar_hidden": self.qs.value("ui/statusbar_hidden", "0"),
            "minimal_mode": self.qs.value("ui/minimal_mode", "0"),
            "plugins_enabled": self.qs.value("plugins/enabled", ""),
        }
    def save(self, key, value):
        self.qs.setValue(key, value)

# =============================
# System Theme Watcher
# =============================
class SystemThemeWatcher(QWidget):
    themeChanged = pyqtSignal(str)  # 'dark' | 'light'
    def __init__(self, parent=None):
        super().__init__(parent)
        self._last = self.get_system_theme()
        self._use_signal = False
        try:
            hints = QGuiApplication.styleHints()
            if hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(self._on_scheme_changed)
                self._use_signal = True
        except Exception:
            self._use_signal = False
        if not self._use_signal:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._poll)
            self._timer.start(2000)
    def _on_scheme_changed(self, scheme):
        try:
            mode = 'dark' if scheme == Qt.ColorScheme.Dark else 'light'
        except Exception:
            mode = self.get_system_theme()
        if mode != self._last:
            self._last = mode
            self.themeChanged.emit(mode)
    def _poll(self):
        mode = self.get_system_theme()
        if mode != self._last:
            self._last = mode
            self.themeChanged.emit(mode)
    def get_system_theme(self) -> str:
        try:
            scheme = QGuiApplication.styleHints().colorScheme()
            return 'dark' if scheme == Qt.ColorScheme.Dark else 'light'
        except Exception:
            pass
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                    val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                    return 'light' if int(val) == 1 else 'dark'
            except Exception:
                return 'light'
        if sys.platform == "darwin":
            try:
                out = subprocess.check_output(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                              text=True, stderr=subprocess.STDOUT).strip()
                return 'dark' if "Dark" in out else 'light'
            except subprocess.CalledProcessError:
                return 'light'
            except Exception:
                return 'light'
        return 'light'

# =============================
# FFmpeg 分析线程
# =============================
class FFmpegAnalysisThread(QThread):
    progress_updated = pyqtSignal(int)
    analysis_finished = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path
        self._is_running = True
        self.process = None
    def stop(self):
        self._is_running = False
        if self.process:
            try:
                parent = psutil.Process(self.process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    child.terminate()
                parent.terminate()
            except:
                pass
    def run(self):
        try:
            if not os.path.exists(self.file_path):
                self.error_occurred.emit(f"文件不存在: {self.file_path}")
                return
            try:
                with open(self.file_path, 'rb') as f:
                    if f.read(100) == b'':
                        self.error_occurred.emit("文件可能已损坏或为空")
                        return
            except IOError:
                self.error_occurred.emit("文件无法读取，可能已被占用或损坏")
                return
            if not self._is_running: return
            self.progress_updated.emit(10)
            file_info = self.get_file_info()
            if not self._is_running: return
            self.progress_updated.emit(30)
            media_info = self.get_media_info()
            if not self._is_running: return
            self.progress_updated.emit(70)
            result = self.merge_info(file_info, media_info)
            if not self._is_running: return
            self.progress_updated.emit(100)
            self.analysis_finished.emit(result)
        except Exception as e:
            if self._is_running:
                self.error_occurred.emit(f"分析视频时出错: {str(e)}")
    def get_file_info(self):
        filename = os.path.basename(self.file_path)
        name_without_ext = os.path.splitext(filename)[0]
        title = name_without_ext
        subtitle = "无"
        separators = ['_', '-', '.', ' ']
        for sep in separators:
            if sep in name_without_ext:
                parts = name_without_ext.split(sep)
                if len(parts) >= 2:
                    title = parts[0]
                    subtitle = sep.join(parts[1:])
                    break
        file_size = os.path.getsize(self.file_path)
        size_mb = file_size / (1024 * 1024)
        return {
            "title": title,
            "subtitle": subtitle,
            "filename": filename,
            "file_path": self.file_path,
            "file_size": f"{size_mb:.2f} MB",
            "file_size_bytes": file_size
        }
    def get_media_info(self):
        try:
            cmd = ['ffprobe','-v','error','-print_format','json','-show_format','-show_streams', self.file_path]
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='ignore', env=env, bufsize=1
            )
            try:
                stdout, stderr = self.process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                stdout, stderr = self.process.communicate()
                raise Exception("FFprobe执行超时，文件可能过大或损坏")
            if self.process.returncode != 0:
                error_msg = stderr if stderr else "未知错误"
                raise Exception(f"FFprobe执行失败 (返回码: {self.process.returncode}): {error_msg}")
            if not stdout or stdout.strip() == "":
                raise Exception("FFprobe输出为空，可能文件格式不支持或已损坏")
            try:
                media_data = json.loads(stdout)
            except json.JSONDecodeError:
                return self.get_basic_media_info()
            return self.parse_ffprobe_output(media_data)
        except Exception as e:
            if "超时" not in str(e):
                try:
                    return self.get_basic_media_info()
                except:
                    pass
            raise e
    def get_basic_media_info(self):
        try:
            cmd = [
                'ffprobe','-v','quiet',
                '-show_entries','format=duration,size,bit_rate:stream=codec_type,codec_name,width,height',
                '-of','default=noprint_wrappers=1:nokey=1',
                self.file_path
            ]
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='ignore', env=env, bufsize=1
            )
            try:
                stdout, stderr = self.process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate()
                raise Exception("获取基本信息超时")
            lines = [line.strip() for line in stdout.split('\n') if line.strip()]
            video_info = {
                "duration": "未知","resolution": "未知","video_codec": "未知","audio_codec": "未知",
                "format": "未知","frame_rate": "未知","audio_sample_rate": "未知",
                "audio_bitrate": "未知","audio_channels": "未知","has_subtitles": False,"bit_rate": "未知"
            }
            if len(lines) >= 5:
                try:
                    video_info['duration'] = self.format_duration(float(lines[0])) if lines[0] != 'N/A' else "未知"
                except:
                    video_info['duration'] = "未知"
                video_info['bit_rate'] = self.format_bitrate(lines[2]) if lines[2] != 'N/A' else "未知"
                for i, line in enumerate(lines[3:]):
                    if line == 'video':
                        if i+4 < len(lines):
                            video_info['video_codec'] = lines[i+4] if lines[i+4] != 'N/A' else "未知"
                            if i+5 < len(lines) and i+6 < len(lines):
                                width = lines[i+5] if lines[i+5] != 'N/A' else '0'
                                height = lines[i+6] if lines[i+6] != 'N/A' else '0'
                                video_info['resolution'] = f"{width}x{height}"
                    elif line == 'audio':
                        if i+4 < len(lines):
                            video_info['audio_codec'] = lines[i+4] if lines[i+4] != 'N/A' else "未知"
            return video_info
        except Exception:
            return {
                "duration": "解析失败","resolution": "解析失败","video_codec": "解析失败","audio_codec": "解析失败",
                "format": "解析失败","frame_rate": "解析失败","audio_sample_rate": "解析失败",
                "audio_bitrate": "解析失败","audio_channels": "解析失败","has_subtitles": False,"bit_rate": "解析失败"
            }
    def parse_ffprobe_output(self, media_data):
        video_info = {
            "duration": "未知","resolution": "未知","video_codec": "未知","audio_codec": "未知",
            "format": "未知","frame_rate": "未知","audio_sample_rate": "未知",
            "audio_bitrate": "未知","audio_channels": "未知","has_subtitles": False,"bit_rate": "未知"
        }
        if media_data is None:
            return video_info
        if 'format' in media_data:
            format_info = media_data['format']
            video_info['format'] = format_info.get('format_name', '未知')
            video_info['bit_rate'] = self.format_bitrate(format_info.get('bit_rate'))
            duration = format_info.get('duration')
            if duration:
                try:
                    video_info['duration'] = self.format_duration(float(duration))
                except (ValueError, TypeError):
                    video_info['duration'] = "未知"
        video_streams, audio_streams, subtitle_streams = [], [], []
        for stream in media_data.get('streams', []):
            ct = stream.get('codec_type')
            if ct == 'video': video_streams.append(stream)
            elif ct == 'audio': audio_streams.append(stream)
            elif ct == 'subtitle': subtitle_streams.append(stream)
        if video_streams:
            vs = video_streams[0]
            video_info['video_codec'] = vs.get('codec_name', '未知').upper()
            w, h = vs.get('width', 0), vs.get('height', 0)
            if w and h: video_info['resolution'] = f"{w}x{h}"
            fr = self.calculate_frame_rate(vs)
            if fr: video_info['frame_rate'] = f"{fr:.2f} fps"
        if audio_streams:
            as_ = audio_streams[0]
            video_info['audio_codec'] = as_.get('codec_name', '未知').upper()
            sr = as_.get('sample_rate')
            if sr: video_info['audio_sample_rate'] = f"{int(sr)} Hz"
            ch = as_.get('channels')
            if ch: video_info['audio_channels'] = ch
            br = as_.get('bit_rate')
            if br: video_info['audio_bitrate'] = self.format_bitrate(br)
        video_info['has_subtitles'] = len(subtitle_streams) > 0
        return video_info
    def calculate_frame_rate(self, video_stream):
        afr = video_stream.get('avg_frame_rate')
        if afr and '/' in afr:
            try:
                num, den = map(int, afr.split('/'))
                if den != 0: return num/den
            except (ValueError, ZeroDivisionError): pass
        rfr = video_stream.get('r_frame_rate')
        if rfr and '/' in rfr:
            try:
                num, den = map(int, rfr.split('/'))
                if den != 0: return num/den
            except (ValueError, ZeroDivisionError): pass
        return None
    def format_duration(self, seconds):
        try:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        except (ValueError, TypeError):
            return "未知"
    def format_bitrate(self, bit_rate):
        if bit_rate:
            try:
                bit_rate = int(bit_rate)
                if bit_rate >= 1_000_000:
                    return f"{bit_rate/1_000_000:.1f} Mbps"
                elif bit_rate >= 1000:
                    return f"{bit_rate/1000:.0f} kbps"
                else:
                    return f"{bit_rate} bps"
            except (ValueError, TypeError):
                pass
        return "未知"
    def merge_info(self, file_info, media_info):
        return {**file_info, **media_info}

# =============================
# 缩略图生成线程
# =============================
class ThumbnailGeneratorThread(QThread):
    thumbnail_generated = pyqtSignal(str, QPixmap)
    error_occurred = pyqtSignal(str, str)
    def __init__(self, file_path, size: QSize = QSize(80, 45), corner_radius=5, quality=3):
        super().__init__()
        self.file_path = file_path
        self.size = size
        self.corner_radius = corner_radius
        self.quality = quality
        self._is_running = True
        self.process = None
    def stop(self):
        self._is_running = False
        if self.process:
            try:
                parent = psutil.Process(self.process.pid)
                for child in parent.children(recursive=True):
                    child.terminate()
                parent.terminate()
            except:
                pass
    def apply_rounded_corners(self, pixmap: QPixmap, radius: int = 5) -> QPixmap:
        if pixmap.isNull():
            return pixmap
        target = QPixmap(pixmap.size())
        target.fill(Qt.GlobalColor.transparent)
        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        path = QPainterPath()
        rect = QRectF(target.rect())
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        return target
    def run(self):
        try:
            if not self._is_running:
                return
            cmd = [
                'ffmpeg','-i', self.file_path,
                '-ss','00:00:01','-vframes','1',
                '-f','image2pipe','-vcodec','mjpeg',
                '-s', f'{self.size.width()}x{self.size.height()}',
                '-q:v', str(self.quality),
                '-vf','scale=flags=lanczos',
                '-threads','1','-'
            ]
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, bufsize=1024*1024
            )
            try:
                image_data = self.process.stdout.read()
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.error_occurred.emit(self.file_path, "FFmpeg生成缩略图超时")
                return
            if not self._is_running:
                return
            if self.process.returncode != 0:
                self.error_occurred.emit(self.file_path, "FFmpeg生成缩略图失败或文件受保护")
                return
            if image_data:
                pixmap = QPixmap()
                if pixmap.loadFromData(image_data, "JPG"):
                    rounded_pixmap = self.apply_rounded_corners(pixmap, self.corner_radius)
                    self.thumbnail_generated.emit(self.file_path, rounded_pixmap)
                else:
                    self.error_occurred.emit(self.file_path, "无法从数据加载缩略图")
            else:
                self.error_occurred.emit(self.file_path, "FFmpeg没有输出图像数据")
        except Exception as e:
            if self._is_running:
                self.error_occurred.emit(self.file_path, f"生成缩略图时出错: {str(e)}")

# =============================
# 纵向大卡片组件（不分组）
# =============================
class CollapsibleCard(QFrame):
    def __init__(self, title: str, rows: list[tuple[str, str]], parent=None, collapsible=True):
        super().__init__(parent)
        self.setObjectName("cardFrame")
        self._rows = rows
        self._collapsible = collapsible

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # 标题条
        self.header = QFrame()
        self.header.setObjectName("cardHeader")
        h = QHBoxLayout(self.header)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)

        self.title_lab = QLabel(title)
        self.title_lab.setObjectName("cardTitle")
        h.addWidget(self.title_lab)

        h.addStretch(1)

        self.meta_lab = QLabel("完整视图")
        self.meta_lab.setObjectName("cardMeta")
        h.addWidget(self.meta_lab)

        self.copy_btn = QPushButton("复制本卡片")
        self.copy_btn.setObjectName("cardAction")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self.copy_card)
        h.addWidget(self.copy_btn)

        if collapsible:
            self.toggle_btn = QPushButton("收起")
            self.toggle_btn.setObjectName("cardToggle")
            self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.toggle_btn.clicked.connect(self.toggle_body)
            h.addWidget(self.toggle_btn)

        root.addWidget(self.header)

        # 内容区
        self.body = QWidget()
        self.body.setObjectName("cardBody")
        self.grid = QVBoxLayout(self.body)
        self.grid.setContentsMargins(10, 8, 10, 10)
        self.grid.setSpacing(6)

        # 两列 Key:Value 行
        for k, v in rows:
            row_frame = QFrame()
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(10)
            k_lab = QLabel(k)
            k_lab.setObjectName("kvKey")
            v_lab = QLabel(str(v))
            v_lab.setObjectName("kvVal")
            v_lab.setWordWrap(True)
            row_layout.addWidget(k_lab, 0)
            row_layout.addWidget(v_lab, 1)
            self.grid.addWidget(row_frame)

        root.addWidget(self.body)

    def toggle_body(self):
        vis = not self.body.isVisible()
        self.body.setVisible(vis)
        if hasattr(self, 'toggle_btn'):
            self.toggle_btn.setText("展开" if not vis else "收起")

    def copy_card(self):
        lines = [self.title_lab.text()]
        lines.append("-" * max(8, len(self.title_lab.text())))
        for i in range(self.grid.count()):
            row_item = self.grid.itemAt(i)
            row_w = row_item.widget()
            if not row_w:
                continue
            lay = row_w.layout()
            if lay and lay.count() >= 2:
                k_w = lay.itemAt(0).widget()
                v_w = lay.itemAt(1).widget()
                if k_w and v_w:
                    lines.append(f"{k_w.text()}: {v_w.text()}")
        QApplication.clipboard().setText("\n".join(lines))
        p = self.window()
        if hasattr(p, 'status_bar') and p.status_bar.isVisible():
            p.status_bar.showMessage(f"已复制「{self.title_lab.text()}」信息", 2500)

# =============================
# 单个视频信息标签页 —— 仅一张大卡片
# =============================
class VideoInfoTab(QWidget):
    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path
        self.video_info = {}
        self.analysis_thread = None
        self.setup_ui()
        self.analyze_video()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(12)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        self.cancel_button = QPushButton("取消分析")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_analysis)
        self.cancel_button.setFixedHeight(32)
        layout.addWidget(self.cancel_button)

        self.error_text = QTextEdit()
        self.error_text.setVisible(False)
        self.error_text.setReadOnly(True)
        layout.addWidget(self.error_text)

        self.info_scroll = QScrollArea()
        self.info_scroll.setWidgetResizable(True)
        self.info_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.info_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.info_container = QWidget()
        self.info_layout = QVBoxLayout(self.info_container)
        self.info_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.info_layout.setSpacing(10)
        self.info_layout.setContentsMargins(8, 8, 8, 8)

        self.initial_label = QLabel("正在分析视频文件...")
        self.initial_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.initial_label.setObjectName("initialLabel")
        self.info_layout.addWidget(self.initial_label)

        self.info_scroll.setWidget(self.info_container)
        layout.addWidget(self.info_scroll)

    def analyze_video(self):
        self.progress_bar.setVisible(True)
        self.cancel_button.setVisible(True)
        self.analysis_thread = FFmpegAnalysisThread(self.file_path)
        self.analysis_thread.progress_updated.connect(self.update_progress)
        self.analysis_thread.analysis_finished.connect(self.display_video_info)
        self.analysis_thread.error_occurred.connect(self.display_error)
        self.analysis_thread.start()

    def cancel_analysis(self):
        if self.analysis_thread and self.analysis_thread.isRunning():
            self.analysis_thread.stop()
            self.analysis_thread.quit()
            self.analysis_thread.wait(5000)
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.initial_label.setText("分析已取消")
        if hasattr(self.window(), 'status_bar'):
            self.window().status_bar.showMessage("分析已取消", 3000)

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def display_video_info(self, video_info):
        self.video_info = video_info
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.error_text.setVisible(False)
        for i in reversed(range(self.info_layout.count())):
            w = self.info_layout.itemAt(i).widget()
            if w: w.setParent(None)
        self.create_beautiful_info_layout()

    # ------- 单张大卡片布局 -------
    def create_beautiful_info_layout(self):
        self.info_layout.setSpacing(10)
        self.info_layout.setContentsMargins(8, 8, 8, 8)

        v = self.video_info
        rows = [
            ("文件名", v["filename"]),
            ("大小", v["file_size"]),
            ("时长", v["duration"]),
            ("分辨率", v["resolution"]),
            ("视频标题", v["title"]),
            ("副标题", v["subtitle"]),
            ("容器格式", v["format"]),
            ("帧率", v["frame_rate"]),
            ("总比特率", v["bit_rate"]),
            ("视频编码", v["video_codec"]),
            ("音频编码", v["audio_codec"]),
            ("音频采样率", v["audio_sample_rate"]),
            ("音频比特率", v["audio_bitrate"]),
            ("音频声道数", f"{v.get('audio_channels', '未知')} 声道"),
            ("字幕", "有" if v["has_subtitles"] else "无"),
            ("路径", v["file_path"]),
        ]

        big_card = CollapsibleCard("视频信息（完整）", rows, collapsible=False)
        self.info_layout.addWidget(big_card)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        copy_button = QPushButton("复制所有信息")
        copy_button.setFixedHeight(32)
        copy_button.clicked.connect(self.copy_all_info)
        export_button = QPushButton("导出为文本文件")
        export_button.setFixedHeight(32)
        export_button.clicked.connect(self.export_to_file)
        btn_row.addWidget(copy_button)
        btn_row.addWidget(export_button)
        self.info_layout.addLayout(btn_row)

    def copy_all_info(self):
        v = self.video_info
        text = (f"视频信息 - {v['filename']}\n" + "=" * 50 + "\n\n"
                f"  文件名: {v['filename']}\n"
                f"  文件大小: {v['file_size']}\n"
                f"  时长: {v['duration']}\n"
                f"  分辨率: {v['resolution']}\n"
                f"  视频标题: {v['title']}\n"
                f"  副标题: {v['subtitle']}\n"
                f"  容器格式: {v['format']}\n"
                f"  帧率: {v['frame_rate']}\n"
                f"  总比特率: {v['bit_rate']}\n"
                f"  视频编码: {v['video_codec']}\n"
                f"  音频编码: {v['audio_codec']}\n"
                f"  音频采样率: {v['audio_sample_rate']}\n"
                f"  音频比特率: {v['audio_bitrate']}\n"
                f"  音频声道数: {v.get('audio_channels','未知')} 声道\n"
                f"  字幕: {'有' if v['has_subtitles'] else '无'}\n"
                f"  路径: {v['file_path']}\n")
        QApplication.clipboard().setText(text)
        if hasattr(self.window(), 'status_bar'):
            self.window().status_bar.showMessage("已复制所有信息到剪贴板", 3000)

    def export_to_file(self):
        v = self.video_info
        video_name = os.path.splitext(v['filename'])[0]
        default_filename = f"{video_name}.txt"
        filename = QFileDialog.getSaveFileName(
            self, "导出视频信息", default_filename,
            "文本文件 (*.txt);;所有文件 (*)"
        )[0]
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(f"视频信息 - {v['filename']}\n")
                    f.write("=" * 50 + "\n\n")
                    f.write(f"  文件名: {v['filename']}\n")
                    f.write(f"  文件大小: {v['file_size']}\n")
                    f.write(f"  时长: {v['duration']}\n")
                    f.write(f"  分辨率: {v['resolution']}\n")
                    f.write(f"  视频标题: {v['title']}\n")
                    f.write(f"  副标题: {v['subtitle']}\n")
                    f.write(f"  容器格式: {v['format']}\n")
                    f.write(f"  帧率: {v['frame_rate']}\n")
                    f.write(f"  总比特率: {v['bit_rate']}\n")
                    f.write(f"  视频编码: {v['video_codec']}\n")
                    f.write(f"  音频编码: {v['audio_codec']}\n")
                    f.write(f"  音频采样率: {v['audio_sample_rate']}\n")
                    f.write(f"  音频比特率: {v['audio_bitrate']}\n")
                    f.write(f"  音频声道数: {v.get('audio_channels','未知')} 声道\n")
                    f.write(f"  字幕: {'有' if v['has_subtitles'] else '无'}\n")
                    f.write(f"  路径: {v['file_path']}\n")
                if hasattr(self.window(), 'status_bar') and self.window().status_bar.isVisible():
                    self.window().status_bar.showMessage(f"已导出信息到: {filename}", 5000)
                QMessageBox.information(self, "导出成功", f"视频信息已成功导出到:\n{filename}")
            except Exception as e:
                QMessageBox.critical(self, "导出失败", f"导出文件时出错:\n{str(e)}")

    def display_error(self, error_message):
        self.progress_bar.setVisible(False)
        self.cancel_button.setVisible(False)
        self.initial_label.setVisible(False)
        self.error_text.setVisible(True)
        self.error_text.setPlainText(
            f"分析视频时出现错误:\n\n{error_message}\n\n请确保：\n1. FFmpeg已正确安装\n2. 视频文件没有损坏\n3. 文件路径不包含特殊字符\n4. 视频格式受支持\n\n"
            "如果问题持续存在，请尝试：\n• 重新安装FFmpeg\n• 使用其他视频文件测试\n• 检查文件权限"
        )

    def closeEvent(self, event):
        if self.analysis_thread and self.analysis_thread.isRunning():
            self.analysis_thread.stop()
            self.analysis_thread.quit()
            self.analysis_thread.wait(3000)
        event.accept()

# =============================
# 插件管理器对话框
# =============================
class PluginManagerDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("插件管理器")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.viewer: "VideoInfoViewer" = parent
        lay = QVBoxLayout(self)
        tip = QLabel("在程序目录下的 plugins/ 文件夹中放置 .py 插件。\n"
                     "每个插件需定义：PLUGIN_NAME，register(app)，可选 unregister(app, handle)。")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.list = QListWidget()
        lay.addWidget(self.list)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.refresh()
    def refresh(self):
        self.viewer.scan_plugins()
        enabled = self.viewer._read_enabled_plugins()
        self.list.clear()
        for name in sorted(self.viewer.plugins_found.keys()):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in enabled else Qt.CheckState.Unchecked)
            self.list.addItem(item)
    def get_enabled_set(self):
        s = set()
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                s.add(it.text())
        return s

# =============================
# 主窗口（跟随系统主题）
# =============================
class VideoInfoViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings_api = AppSettings()
        self.settings = self.settings_api.load()
        self.video_files = []
        self.always_on_top = False
        self.thumbnail_threads = {}
               # 队列与并发控制
        self.thumbnail_queue = []
        self.max_concurrent_thumbnails = 3
        self.active_thumbnail_threads = 0

        self._splitter_sizes_backup = None
        self._statusbar_hidden_before_minimal = False
        self._toolbar_visible_before_minimal = True
        self._prev_window_state = None

        self.plugins_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
        self.plugins_loaded = {}
        self.plugins_found = {}

        self.theme = "light"
        self.theme_watcher = SystemThemeWatcher(self)
        self.theme_watcher.themeChanged.connect(self.set_theme)

        self.setup_ui()
        self.setAcceptDrops(True)
        self.check_ffmpeg()
        self.setup_hotkeys()
        self.restore_window_state()
        self.set_theme(self.theme_watcher.get_system_theme(), announce=False)
        self.load_plugins_on_start()

    # ============ 主题应用 ============
    def set_theme(self, mode: str, announce: bool = True):
        mode = "dark" if mode == "dark" else "light"
        if self.theme == mode:
            return
        self.theme = mode
        self.apply_theme()
        if announce and hasattr(self, 'status_bar') and self.status_bar.isVisible():
            self.status_bar.showMessage(f"已切换到 {'暗色 🌙' if self.theme=='dark' else '浅色 ☀️'}（跟随系统）", 1500)
    def apply_theme(self):
        if self.theme == "dark":
            self.apply_dark_theme()
        else:
            self.apply_light_theme()

    # —— 深色主题 ——（含卡片样式）
    def apply_dark_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(40, 44, 52))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(171, 178, 191))
        palette.setColor(QPalette.ColorRole.Base, QColor(30, 32, 40))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(40, 44, 52))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(40, 44, 52))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(171, 178, 191))
        palette.setColor(QPalette.ColorRole.Text, QColor(171, 178, 191))
        palette.setColor(QPalette.ColorRole.Button, QColor(50, 54, 62))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(171, 178, 191))
        palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
        palette.setColor(QPalette.ColorRole.Link, QColor(97, 175, 239))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(97, 175, 239))
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
        QApplication.setPalette(palette)
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #282c34;
                color: #abb2bf;
                font-family: "Microsoft YaHei", "Segoe UI", "San Francisco", "Helvetica Neue", sans-serif;
            }
            QLabel#fileListLabel, QLabel#propertiesLabel {
                font-size: 14px; font-weight: bold; color: #61afef;
                padding: 5px; background-color: #2c313a; border-radius: 4px;
            }
            QToolBar QLabel { background: transparent; color: #abb2bf; }
            QToolBar {
                background-color: #2c313a; border: none; border-bottom: 1px solid #3e4451;
                spacing: 8px; padding: 8px;
            }
            QToolBar QToolButton {
                background-color: #3e4451; color: #abb2bf; border: none; border-radius: 6px;
                padding: 8px 12px; font-weight: bold;
            }
            QToolBar QToolButton:hover { background-color: #4b5263; }
            QToolBar QToolButton:pressed { background-color: #565c6d; }
            QToolBar QToolButton:checked { background-color: #61afef; color: #282c34; }
            QSplitter::handle { background-color: #3e4451; width: 4px; border-radius: 2px; }
            QSplitter::handle:hover { background-color: #61afef; }
            QListWidget {
                background-color: #2c313a; color: #abb2bf; border: none; border-radius: 8px; outline: none;
            }
            QListWidget::item { background-color: transparent; border: none; border-radius: 6px;
                padding: 5px; margin: 2px; min-height: 50px; }
            QListWidget::item:selected { background-color: #3e4451; color: #61afef; }
            QListWidget::item:hover { background-color: #353b45; }
            QTabWidget::pane { border: none; background-color: #282c34; border-radius: 8px; }
            QTabBar::tab {
                background-color: #2c313a; color: #abb2bf; padding: 10px 15px; margin-right: 2px;
                border-top-left-radius: 8px; border-top-right-radius: 8px; font-weight: bold;
            }
            QTabBar::tab:selected { background-color: #3e4451; color: #61afef; }
            QTabBar::tab:hover { background-color: #353b45; }
            QPushButton {
                background-color: #3e4451; color: #abb2bf; border: none; border-radius: 6px;
                padding: 8px 16px; font-weight: bold; min-width: 80px;
            }
            QPushButton:hover { background-color: #4b5263; }
            QPushButton:pressed { background-color: #565c6d; }
            QPushButton:focus { outline: none; border: 1px solid #61afef; }
            QProgressBar { border: none; border-radius: 4px; background-color: #2c313a; color: #abb2bf; }
            QProgressBar::chunk { background-color: #61afef; border-radius: 4px; }
            QTextEdit {
                background-color: #2c313a; color: #abb2bf; border: 1px solid #3e4451;
                border-radius: 6px; padding: 10px; font-family: "Consolas","Monaco",monospace;
            }
            QMenu#contextMenu {
                background-color: #2c313a; color: #abb2bf; border: 1px solid #3e4451; border-radius: 6px;
            }
            QMenu::item { padding: 6px 20px; border-radius: 4px; }
            QMenu::item:selected { background-color: #3e4451; }

            /* --- 纵向大卡片（深色） --- */
            QFrame#cardFrame {
                background-color: #1f232b;
                border: 1px solid #3e4451;
                border-radius: 12px;
            }
            QFrame#cardHeader {
                background-color: rgba(97,175,239,0.10);
                border: 1px solid rgba(97,175,239,0.20);
                border-radius: 9px;
                margin: 2px 2px 0 2px;
            }
            QLabel#cardTitle { font-weight: 600; color: #d7e7ff; }
            QLabel#cardMeta { color: #9aa4b2; font-size: 11px; }
            QPushButton#cardAction, QPushButton#cardToggle {
                padding: 4px 8px; border-radius: 6px; background-color: #3e4451; color: #cfd6e1;
            }
            QPushButton#cardAction:hover, QPushButton#cardToggle:hover { background-color: #4b5263; }
            QWidget#cardBody { background: transparent; }
            QLabel#kvKey { color: #9aa4b2; font-size: 12px; min-width: 72px; }
            QLabel#kvVal { color: #cfd6e1; font-size: 13px; }
        """)

    # —— 浅色主题 ——（含卡片样式）
    def apply_light_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.black)
        palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(245, 245, 245))
        palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.black)
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.black)
        palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.black)
        palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
        palette.setColor(QPalette.ColorRole.Link, QColor(0, 120, 215))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 120, 215))
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
        QApplication.setPalette(palette)
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #f0f0f0;
                color: #000000;
                font-family: "Microsoft YaHei", "Segoe UI", "San Francisco", "Helvetica Neue", sans-serif;
            }
            QLabel#fileListLabel, QLabel#propertiesLabel {
                font-size: 14px; font-weight: bold; color: #004e8c;
                padding: 5px; background-color: #e8e8e8; border-radius: 4px;
            }
            QToolBar QLabel { background: transparent; color: #000000; }
            QToolBar {
                background-color: #e8e8e8; border: none; border-bottom: 1px solid #d0d0d0;
                spacing: 8px; padding: 8px;
            }
            QToolBar QToolButton {
                background-color: #d0d0d0; color: #000000; border: none; border-radius: 6px;
                padding: 8px 12px; font-weight: bold;
            }
            QToolBar QToolButton:hover { background-color: #b0b0b0; }
            QToolBar QToolButton:pressed { background-color: #a0a0a0; }
            QToolBar QToolButton:checked { background-color: #0078d7; color: white; }
            QSplitter::handle { background-color: #d0d0d0; width: 4px; border-radius: 2px; }
            QSplitter::handle:hover { background-color: #0078d7; }
            QListWidget {
                background-color: #ffffff; color: #000000; border: 1px solid #d0d0d0; border-radius: 8px; outline: none;
            }
            QListWidget::item { background-color: transparent; border: none; border-radius: 6px;
                padding: 5px; margin: 2px; min-height: 50px; }
            QListWidget::item:selected { background-color: #e6f4ff; color: #004e8c; }
            QListWidget::item:hover { background-color: #f0f0f0; }
            QTabWidget::pane { border: 1px solid #d0d0d0; background-color: #ffffff; border-radius: 8px; }
            QTabBar::tab {
                background-color: #e8e8e8; color: #000000; padding: 10px 15px; margin-right: 2px;
                border-top-left-radius: 8px; border-top-right-radius: 8px; font-weight: bold;
            }
            QTabBar::tab:selected { background-color: #ffffff; color: #004e8c; border-bottom: 2px solid #0078d7; }
            QTabBar::tab:hover { background-color: #d0d0d0; }
            QPushButton {
                background-color: #d0d0d0; color: #000000; border: none; border-radius: 6px;
                padding: 8px 16px; font-weight: bold; min-width: 80px;
            }
            QPushButton:hover { background-color: #b0b0b0; }
            QPushButton:pressed { background-color: #a0a0a0; }
            QPushButton:focus { outline: none; border: 1px solid #0078d7; }
            QProgressBar { border: 1px solid #d0d0d0; border-radius: 4px; background-color: #ffffff; color: #000000; }
            QProgressBar::chunk { background-color: #0078d7; border-radius: 4px; }
            QTextEdit {
                background-color: #ffffff; color: #000000; border: 1px solid #d0d0d0;
                border-radius: 6px; padding: 10px; font-family: "Consolas","Monaco",monospace;
            }
            QMenu#contextMenu {
                background-color: #ffffff; color: #000000; border: 1px solid #d0d0d0; border-radius: 6px;
            }
            QMenu::item { padding: 6px 20px; border-radius: 4px; }
            QMenu::item:selected { background-color: #e6f4ff; }

            /* --- 纵向大卡片（浅色） --- */
            QFrame#cardFrame {
                background-color: #ffffff;
                border: 1px solid #dcdcdc;
                border-radius: 12px;
            }
            QFrame#cardHeader {
                background-color: rgba(0,120,215,0.08);
                border: 1px solid rgba(0,120,215,0.18);
                border-radius: 9px;
                margin: 2px 2px 0 2px;
            }
            QLabel#cardTitle { font-weight: 600; color: #084f8c; }
            QLabel#cardMeta { color: #666; font-size: 11px; }
            QPushButton#cardAction, QPushButton#cardToggle {
                padding: 4px 8px; border-radius: 6px; background-color: #eaeaea; color: #111;
            }
            QPushButton#cardAction:hover, QPushButton#cardToggle:hover { background-color: #dedede; }
            QWidget#cardBody { background: transparent; }
            QLabel#kvKey { color: #6b6b6b; font-size: 12px; min-width: 72px; }
            QLabel#kvVal { color: #111; font-size: 13px; }
        """)

    # ============ 插件相关 ============
    def _read_enabled_plugins(self):
        raw = self.settings_api.qs.value("plugins/enabled", "")
        names = [s.strip() for s in raw.split(",") if s.strip()]
        return set(names)
    def _write_enabled_plugins(self, names_set):
        raw = ",".join(sorted(names_set))
        self.settings_api.save("plugins/enabled", raw)
    def scan_plugins(self):
        self.plugins_found.clear()
        if not os.path.isdir(self.plugins_dir):
            try: os.makedirs(self.plugins_dir, exist_ok=True)
            except: pass
            return
        for fn in os.listdir(self.plugins_dir):
            if not fn.endswith(".py"):
                continue
            path = os.path.join(self.plugins_dir, fn)
            try:
                name = None
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for _ in range(40):
                        line = f.readline()
                        if not line: break
                        if "PLUGIN_NAME" in line and "=" in line:
                            name = line.split("=",1)[1].strip().strip("\"' ")
                            break
                if not name:
                    name = os.path.splitext(fn)[0]
                self.plugins_found[name] = {"path": path}
            except:
                pass
    def load_plugin(self, name):
        if name in self.plugins_loaded:
            return True
        meta = self.plugins_found.get(name)
        if not meta:
            return False
        path = meta["path"]
        try:
            spec = importlib.util.spec_from_file_location(f"vip_plugin_{name}", path)
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)
            handle = None
            if hasattr(module, "register") and callable(module.register):
                handle = module.register(self)
            self.plugins_loaded[name] = {"module": module, "handle": handle}
            if hasattr(self, 'status_bar') and self.status_bar.isVisible():
                self.status_bar.showMessage(f"已加载插件：{name}", 2000)
            return True
        except Exception:
            traceback.print_exc()
            QMessageBox.critical(self, "插件加载失败", f"加载插件失败：{name}\n\n{traceback.format_exc()}")
            return False
    def unload_plugin(self, name):
        item = self.plugins_loaded.pop(name, None)
        if not item:
            return
        module = item.get("module")
        handle = item.get("handle")
        try:
            if module and hasattr(module, "unregister") and callable(module.unregister):
                module.unregister(self, handle)
        except Exception:
            traceback.print_exc()
    def reload_plugins(self):
        for name in list(self.plugins_loaded.keys()):
            self.unload_plugin(name)
        self.scan_plugins()
        enabled = self._read_enabled_plugins()
        for name in enabled:
            if name in self.plugins_found:
                self.load_plugin(name)
    def open_plugins_manager(self):
        dlg = PluginManagerDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_enabled = dlg.get_enabled_set()
            old_enabled = self._read_enabled_plugins()
            self._write_enabled_plugins(new_enabled)
            for name in list(old_enabled - new_enabled):
                self.unload_plugin(name)
            for name in list(new_enabled - old_enabled):
                if name in self.plugins_found:
                    self.load_plugin(name)
            if hasattr(self, 'status_bar') and self.status_bar.isVisible():
                self.status_bar.showMessage("插件配置已更新", 2000)
    def load_plugins_on_start(self):
        self.scan_plugins()
        for name in self._read_enabled_plugins():
            if name in self.plugins_found:
                self.load_plugin(name)

    # ============ 基础UI ============
    def check_ffmpeg(self):
        try:
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            subprocess.run(['ffprobe','-version'], capture_output=True, check=True,
                           timeout=10, encoding='utf-8', errors='ignore', env=env)
            if hasattr(self, 'status_bar') and self.status_bar.isVisible():
                self.status_bar.showMessage("FFmpeg可用 - 拖拽视频或使用按钮添加", 3500)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            if hasattr(self, 'status_bar') and self.status_bar.isVisible():
                self.status_bar.showMessage("警告: 未找到FFmpeg，请安装并加入PATH", 6000)
            QMessageBox.warning(self, "FFmpeg未找到",
                "未检测到FFmpeg安装。\n\n请安装FFmpeg并确保ffprobe命令可用：\n"
                "1. 从 https://ffmpeg.org 下载FFmpeg\n"
                "2. 将FFmpeg添加到系统PATH环境变量\n"
                "3. 重启应用程序")

    def setup_ui(self):
        self.setWindowTitle("视频信息查看器 - 基于FFmpeg")
        self.setMinimumSize(1000, 700)
        self.menuBar()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        # 左侧列表
        self.file_list_container = QWidget()
        file_list_layout = QVBoxLayout(self.file_list_container)
        file_list_layout.setContentsMargins(0, 0, 0, 0)
        file_list_layout.setSpacing(5)
        file_list_label = QLabel("文件列表")
        file_list_label.setObjectName("fileListLabel")
        file_list_layout.addWidget(file_list_label)

        self.file_list_widget = QListWidget()
        self.file_list_widget.setMaximumWidth(300)
        self.file_list_widget.itemClicked.connect(self.on_file_selected)
        self.file_list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list_widget.customContextMenuRequested.connect(self.show_file_list_context_menu)
        self.file_list_widget.setIconSize(QSize(80, 45))
        self.file_list_widget.setGridSize(QSize(300, 50))
        self.file_list_widget.setViewMode(QListWidget.ViewMode.ListMode)
        self.file_list_widget.setUniformItemSizes(True)
        self.file_list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.file_list_widget.setWordWrap(True)
        file_list_layout.addWidget(self.file_list_widget)
        self.splitter.addWidget(self.file_list_container)

        # 右侧属性（单张大卡片在 Tab 内）
        self.properties_container = QWidget()
        properties_layout = QVBoxLayout(self.properties_container)
        properties_layout.setContentsMargins(0, 0, 0, 0)
        properties_layout.setSpacing(5)
        properties_label = QLabel("文件属性")
        properties_label.setObjectName("propertiesLabel")
        properties_layout.addWidget(properties_label)

        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        self.tab_widget.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        properties_layout.addWidget(self.tab_widget)

        self.splitter.addWidget(self.properties_container)

        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        main_layout.addWidget(self.splitter)

        self.create_toolbar()
        self.create_menus()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    def create_toolbar(self):
        toolbar = QToolBar()
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self.toolbar = toolbar

        add_file_action = QAction("📁 添加文件", self)
        add_file_action.triggered.connect(self.add_video_files)
        toolbar.addAction(add_file_action)

        add_folder_action = QAction("📂 添加文件夹", self)
        add_folder_action.triggered.connect(self.add_video_folder)
        toolbar.addAction(add_folder_action)

        toolbar.addSeparator()

        self.always_on_top_action = QAction("📌 窗口置顶", self)
        self.always_on_top_action.setCheckable(True)
        self.always_on_top_action.triggered.connect(self.toggle_always_on_top)
        toolbar.addAction(self.always_on_top_action)

        toolbar.addSeparator()

        copy_current_action = QAction("📋 复制当前视频信息", self)
        copy_current_action.triggered.connect(self.copy_current_video_info)
        toolbar.addAction(copy_current_action)

        export_current_action = QAction("💾 导出当前视频信息", self)
        export_current_action.triggered.connect(self.export_current_video_info)
        toolbar.addAction(export_current_action)

        export_all_action = QAction("📊 导出所有视频信息", self)
        export_all_action.triggered.connect(self.export_all_video_info)
        toolbar.addAction(export_all_action)

        toolbar.addSeparator()

        clear_list_action = QAction("🗑️ 清空列表", self)
        clear_list_action.triggered.connect(self.clear_file_list)
        toolbar.addAction(clear_list_action)

        toolbar.addSeparator()

        self.toggle_statusbar_action = QAction("📉 隐藏状态栏", self)
        self.toggle_statusbar_action.setCheckable(True)
        self.toggle_statusbar_action.setChecked(False)
        self.toggle_statusbar_action.triggered.connect(self.toggle_statusbar)
        toolbar.addAction(self.toggle_statusbar_action)

        toolbar.addSeparator()

        self.minimal_mode_action = QAction("🧩 简洁模式", self)
        self.minimal_mode_action.setCheckable(True)
        self.minimal_mode_action.setToolTip("进入全屏并隐藏左侧列表、工具栏与状态栏；F11 快捷切换")
        self.minimal_mode_action.triggered.connect(
            lambda: self.apply_minimal_mode(self.minimal_mode_action.isChecked())
        )
        toolbar.addAction(self.minimal_mode_action)

    def create_menus(self):
        view_menu = self.menuBar().addMenu("视图(&V)")
        act_full = QAction("🧩 简洁模式 (F11)", self)
        act_full.setCheckable(True)
        act_full.setChecked(False)
        act_full.triggered.connect(lambda: self.minimal_mode_action.trigger())
        view_menu.addAction(act_full)
        self.minimal_mode_action.toggled.connect(act_full.setChecked)

        plugins_menu = self.menuBar().addMenu("插件(&P)")
        act_manage = QAction("管理插件…", self)
        act_manage.triggered.connect(self.open_plugins_manager)
        plugins_menu.addAction(act_manage)
        act_reload = QAction("重新加载插件", self)
        act_reload.triggered.connect(self.reload_plugins)
        plugins_menu.addAction(act_reload)
        self.plugins_menu = plugins_menu

    def setup_hotkeys(self):
        hotkey_full = QAction("简洁模式（全屏）", self)
        hotkey_full.setShortcut("F11")
        hotkey_full.triggered.connect(lambda: self.minimal_mode_action.trigger())
        self.addAction(hotkey_full)

    def restore_window_state(self):
        status_hidden = (self.settings.get("statusbar_hidden", "0") == "1")
        self.status_bar.setVisible(not status_hidden)
        if hasattr(self, "toggle_statusbar_action"):
            self.toggle_statusbar_action.setChecked(status_hidden)
            self.toggle_statusbar_action.setText("📈 显示状态栏" if status_hidden else "📉 隐藏状态栏")
        minimal_on = (self.settings.get("minimal_mode", "0") == "1")
        if hasattr(self, "minimal_mode_action"):
            self.minimal_mode_action.setChecked(minimal_on)
            self.apply_minimal_mode(minimal_on, init=True)

    # ============ 窗口控制 ============
    def toggle_always_on_top(self, checked):
        self.always_on_top = checked
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()
        status = "已开启" if checked else "已关闭"
        self.status_bar.showMessage(f"窗口置顶 {status}", 2000)
    def toggle_statusbar(self, checked):
        self.status_bar.setVisible(not checked)
        self.settings_api.save("ui/statusbar_hidden", "1" if checked else "0")
        self.toggle_statusbar_action.setText("📈 显示状态栏" if checked else "📉 隐藏状态栏")
    def apply_minimal_mode(self, enable, init=False):
        if enable:
            if not init:
                self._prev_window_state = self.windowState()
                self._splitter_sizes_backup = self.splitter.sizes()
                self._statusbar_hidden_before_minimal = not self.status_bar.isVisible()
                self._toolbar_visible_before_minimal = self.toolbar.isVisible()
            self.file_list_container.setVisible(False)
            self.toolbar.setVisible(False)
            self.status_bar.setVisible(False)
            self.showMaximized()
            self.settings_api.save("ui/minimal_mode", "1")
        else:
            self.file_list_container.setVisible(True)
            self.toolbar.setVisible(self._toolbar_visible_before_minimal)
            self.status_bar.setVisible(not self._statusbar_hidden_before_minimal)
            if self._prev_window_state is not None:
                self.setWindowState(self._prev_window_state)
            if self._splitter_sizes_backup:
                self.splitter.setSizes(self._splitter_sizes_backup)
            self.settings_api.save("ui/minimal_mode", "0")

    # ============ 文件操作 ============
    def add_video_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", "",
            # 核心修改：添加.xl格式支持
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.mpeg *.mpg *.webm *.xl);;所有文件 (*)"
        )
        if files:
            self.add_files_to_list(files)
    def add_video_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if folder:
            # 核心修改：添加.xl格式支持
            video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.mpeg', '.mpg', '.webm', '.xl'}
            files = []
            for root, _, fs in os.walk(folder):
                for f in fs:
                    if Path(f).suffix.lower() in video_extensions:
                        files.append(os.path.join(root, f))
            if files:
                self.add_files_to_list(files)
            else:
                QMessageBox.information(self, "无视频文件", "所选文件夹中未找到支持的视频文件")
    def add_files_to_list(self, files):
        added = 0
        for file_path in files:
            if file_path in self.video_files:
                continue
            self.video_files.append(file_path)
            item = QListWidgetItem(os.path.basename(file_path))
            item.setData(Qt.ItemDataRole.UserRole, file_path)
            self.file_list_widget.addItem(item)
            added += 1
            self.queue_thumbnail_generation(file_path, item)
        if added > 0:
            self.status_bar.showMessage(f"已添加 {added} 个视频文件", 3000)
    def queue_thumbnail_generation(self, file_path, item):
        if file_path in self.thumbnail_threads:
            return
        self.thumbnail_queue.append((file_path, item))
        self.process_thumbnail_queue()
    def process_thumbnail_queue(self):
        while self.active_thumbnail_threads < self.max_concurrent_thumbnails and self.thumbnail_queue:
            file_path, item = self.thumbnail_queue.pop(0)
            if file_path in self.thumbnail_threads:
                continue
            thread = ThumbnailGeneratorThread(file_path)
            thread.thumbnail_generated.connect(self.on_thumbnail_generated)
            thread.error_occurred.connect(self.on_thumbnail_error)
            self.thumbnail_threads[file_path] = (thread, item)
            thread.start()
            self.active_thumbnail_threads += 1
    def on_thumbnail_generated(self, file_path, pixmap):
        if file_path in self.thumbnail_threads:
            thread, item = self.thumbnail_threads.pop(file_path)
            item.setIcon(QIcon(pixmap))
            thread.wait()
            self.active_thumbnail_threads -= 1
            self.process_thumbnail_queue()
    def on_thumbnail_error(self, file_path, error):
        if file_path in self.thumbnail_threads:
            thread, _ = self.thumbnail_threads.pop(file_path)
            thread.wait()
            self.active_thumbnail_threads -= 1
            self.process_thumbnail_queue()
    def on_file_selected(self, item):
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if not file_path:
            return
        for i in range(self.tab_widget.count()):
            if self.tab_widget.widget(i).file_path == file_path:
                self.tab_widget.setCurrentIndex(i)
                return
        tab = VideoInfoTab(file_path)
        tab_name = os.path.basename(file_path)
        index = self.tab_widget.addTab(tab, tab_name)
        self.tab_widget.setCurrentIndex(index)
    def close_tab(self, index):
        widget = self.tab_widget.widget(index)
        if widget:
            widget.close()
        self.tab_widget.removeTab(index)
    def clear_file_list(self):
        for file_path, (thread, _) in list(self.thumbnail_threads.items()):
            thread.stop()
            thread.wait()
        self.thumbnail_threads.clear()
        self.thumbnail_queue.clear()
        self.active_thumbnail_threads = 0
        self.file_list_widget.clear()
        self.video_files = []
        for i in reversed(range(self.tab_widget.count())):
            self.close_tab(i)
        self.status_bar.showMessage("文件列表已清空", 2000)
    def show_file_list_context_menu(self, position):
        if not self.file_list_widget.count():
            return
        menu = QMenu(self)
        menu.setObjectName("contextMenu")
        remove_action = QAction("移除选中项", self)
        remove_action.triggered.connect(self.remove_selected_files)
        menu.addAction(remove_action)
        open_dir_action = QAction("打开文件位置", self)
        open_dir_action.triggered.connect(self.open_file_location)
        menu.addAction(open_dir_action)
        menu.exec(self.file_list_widget.mapToGlobal(position))
    def remove_selected_files(self):
        for item in self.file_list_widget.selectedItems():
            file_path = item.data(Qt.ItemDataRole.UserRole)
            if file_path in self.video_files:
                self.video_files.remove(file_path)
            if file_path in self.thumbnail_threads:
                thread, _ = self.thumbnail_threads.pop(file_path)
                thread.stop()
                thread.wait()
                self.active_thumbnail_threads -= 1
                self.process_thumbnail_queue()
            for i in reversed(range(self.tab_widget.count())):
                if self.tab_widget.widget(i).file_path == file_path:
                    self.close_tab(i)
            row = self.file_list_widget.row(item)
            self.file_list_widget.takeItem(row)
        self.status_bar.showMessage("已移除选中文件", 2000)
    def open_file_location(self):
        items = self.file_list_widget.selectedItems()
        if not items:
            return
        file_path = items[0].data(Qt.ItemDataRole.UserRole)
        if not file_path or not os.path.exists(file_path):
            return
        if sys.platform == "win32":
            os.startfile(os.path.dirname(file_path))
        elif sys.platform == "darwin":
            subprocess.run(["open", os.path.dirname(file_path)])
        else:
            subprocess.run(["xdg-open", os.path.dirname(file_path)])

    # ============ 信息操作 ============
    def copy_current_video_info(self):
        current_tab = self.tab_widget.currentWidget()
        if isinstance(current_tab, VideoInfoTab) and current_tab.video_info:
            current_tab.copy_all_info()
    def export_current_video_info(self):
        current_tab = self.tab_widget.currentWidget()
        if isinstance(current_tab, VideoInfoTab) and current_tab.video_info:
            current_tab.export_to_file()
        else:
            QMessageBox.information(self, "无内容", "请先选择一个视频文件并等待分析完成")
    def export_all_video_info(self):
        if not self.video_files:
            QMessageBox.information(self, "无内容", "文件列表为空，无法导出")
            return
        all_info = []
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, VideoInfoTab) and tab.video_info:
                all_info.append(tab.video_info)
        if not all_info:
            QMessageBox.information(self, "无内容", "没有已分析完成的视频信息")
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "导出所有视频信息", "所有视频信息.txt", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not filename:
            return
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write("所有视频信息汇总\n")
                f.write("=" * 80 + "\n\n")
                for idx, info in enumerate(all_info, 1):
                    f.write(f"[{idx}] {info['filename']}\n")
                    f.write("-" * 60 + "\n")
                    f.write(f"  文件路径: {info['file_path']}\n")
                    f.write(f"  文件大小: {info['file_size']}\n")
                    f.write(f"  时长: {info['duration']}\n")
                    f.write(f"  分辨率: {info['resolution']}\n")
                    f.write(f"  视频编码: {info['video_codec']}\n")
                    f.write(f"  音频编码: {info['audio_codec']}\n")
                    f.write(f"  容器格式: {info['format']}\n")
                    f.write(f"  帧率: {info['frame_rate']}\n")
                    f.write(f"  总比特率: {info['bit_rate']}\n")
                    f.write(f"  字幕: {'有' if info['has_subtitles'] else '无'}\n")
                    f.write("\n")
            self.status_bar.showMessage(f"已导出所有视频信息到: {filename}", 5000)
            QMessageBox.information(self, "导出成功", f"所有视频信息已成功导出到:\n{filename}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出文件时出错:\n{str(e)}")

    # ============ 拖放支持 ============
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    def dropEvent(self, event):
        files = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                file_path = url.toLocalFile()
                if os.path.isfile(file_path):
                    ext = os.path.splitext(file_path)[1].lower()
                    # 核心修改：添加.xl格式支持
                    if ext in ('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.mpeg', '.mpg', '.webm', '.xl'):
                        files.append(file_path)
        if files:
            self.add_files_to_list(files)

    # ============ 窗口关闭 ============
    def closeEvent(self, event):
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, VideoInfoTab) and tab.analysis_thread and tab.analysis_thread.isRunning():
                tab.analysis_thread.stop()
                tab.analysis_thread.quit()
        for file_path, (thread, _) in self.thumbnail_threads.items():
            thread.stop()
            thread.quit()
        event.accept()

# =============================
# 程序入口
# =============================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("视频信息查看器")
    app.setApplicationVersion("1.0")
    window = VideoInfoViewer()
    window.show()
    sys.exit(app.exec())