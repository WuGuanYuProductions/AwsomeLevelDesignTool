import json
import math
import os
import sys
import base64
import inspect
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QMimeData, QPoint, QPointF, QLineF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QBrush, QColor, QDrag, QFont, QIcon, QImage, QKeySequence,
    QPainter, QPainterPath, QPen, QPolygonF, QShortcut
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QKeySequenceEdit, QColorDialog, QComboBox,
    QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGraphicsItem,
    QGraphicsObject, QGraphicsScene, QGraphicsTextItem, QGraphicsView,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget,
    QInputDialog
)
from OpenGL.GL import *
from OpenGL.GLU import gluLookAt, gluPerspective

MIME_SHAPE_TYPE = "application/x-level-shape-type"
CHARACTER_TYPES, PLATFORM_TYPES = {"player", "enemy"}, {"square", "rectangle", "circle", "ellipse", "triangle", "sphere", "cone", "pyramid"}
DEFAULT_CHARACTER_WIDTH, DEFAULT_CHARACTER_HEIGHT, DESIGN_FILE_VERSION = 1.0, 1.8, 2
DEFAULT_SHORTCUTS: Dict[str, str] = {"undo": "Ctrl+Z", "redo": "Ctrl+R", "copy": "Ctrl+C", "cut": "Ctrl+X", "paste": "Ctrl+V"}

BOX_FACES = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 3, 7, 4), (1, 2, 6, 5), (3, 2, 6, 7), (0, 1, 5, 4)]
BOX_EDGES = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]

def to_qpoint(pos) -> QPoint:
    return pos if isinstance(pos, QPoint) else pos.toPoint() if hasattr(pos, "toPoint") else QPoint(int(getattr(pos, "x", lambda: 0)()), int(getattr(pos, "y", lambda: 0)()))


def get_script_dir() -> str:
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


def find_resource_icon() -> QIcon:
    base_dir = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.dirname(os.path.abspath(__file__))
    ico_path = os.path.join(base_dir, "Resources", "icon.ico")
    if os.path.exists(ico_path): 
        return QIcon(ico_path)

    res_dir = os.path.join(get_script_dir(), "resources")
    if os.path.isdir(res_dir):
        for ext in (".ico", ".png", ".jpg", ".jpeg"):
            if f := next((f for f in sorted(os.listdir(res_dir)) if f.lower().endswith(ext)), None):
                return QIcon(os.path.join(res_dir, f))
    return QIcon()


def check_point_inside_platform(shape, px, py) -> bool:
    dx, dy, st = px - shape.x, py - shape.y, shape.shape_type
    if st in {"rectangle", "square", "stair", "pyramid"}: return abs(dx) <= shape.width / 2 and abs(dy) <= shape.height / 2
    if st in {"circle", "sphere", "cone"}: return dx*dx + dy*dy <= (min(shape.width, shape.height)/2)**2
    if st == "ellipse": return (dx*dx) / max(1e-6, (shape.width/2)**2) + (dy*dy) / max(1e-6, (shape.height/2)**2) <= 1.0
    if st == "triangle":
        def sign(p1, p2, p3): return (p1.x()-p3.x())*(p2.y()-p3.y()) - (p2.x()-p3.x())*(p1.y()-p3.y())
        a, b, c = QPointF(shape.x, shape.y-shape.height/2), QPointF(shape.x-shape.width/2, shape.y+shape.height/2), QPointF(shape.x+shape.width/2, shape.y+shape.height/2)
        d1, d2, d3 = sign(QPointF(px, py), a, b), sign(QPointF(px, py), b, c), sign(QPointF(px, py), c, a)
        return not (((d1<0) or (d2<0) or (d3<0)) and ((d1>0) or (d2>0) or (d3>0)))
    return False


def get_real_base_z_with_adjustment(shape, all_shapes, layers) -> float:
    def get_layer_z(idx): return layers[idx].z_offset if 0 <= idx < len(layers) else 0.0
    rz = shape.base_z + get_layer_z(shape.layer_index)
    if shape.shape_type in CHARACTER_TYPES and shape.base_z == 0.0:
        highest_z = rz
        for p in all_shapes:
            if p.shape_type in PLATFORM_TYPES and p.layer_index == shape.layer_index:
                if check_point_inside_platform(p, shape.x, shape.y):
                    pz = p.base_z + get_layer_z(p.layer_index) + p.depth
                    if pz > highest_z: highest_z = pz
        return highest_z
    return rz


@dataclass
class LayerData:
    name: str
    z_offset: float


@dataclass
class ShapeData:
    shape_type: str
    x: float
    y: float
    width: float = 1.0
    height: float = 1.0
    depth: float = 1.0
    color: Tuple[int, int, int] = (100, 170, 255)
    label: str = ""
    font_size: float = 1.0
    layer_index: int = 0
    base_z: float = 0.0
    end_x: float = 0.0
    end_y: float = 0.0
    line_width: float = 0.1
    text_content: str = ""
    text_width: float = 3.0
    text_height: float = 1.0
    stair_start_binding: str = ""
    stair_end_binding: str = ""
    stair_direction: str = "forward"
    stair_auto_height: bool = True
    line_mode: str = "line"
    rotation: float = 0.0
    control_points: List[Tuple[float, float]] = field(default_factory=list)
    image_data: str = ""
    custom_3d_parts: List[Dict] = field(default_factory=list)


class LayerDialog(QDialog):
    def __init__(self, parent: QWidget, current_name: str = "", current_z: float = 0.0):
        super().__init__(parent)
        self.setWindowTitle("图层设置")
        self.resize(320, 130)
        self.name_edit = QLineEdit(current_name)
        self.z_spin = QDoubleSpinBox()
        self.z_spin.setRange(-9999.0, 9999.0)
        self.z_spin.setSingleStep(0.5)
        self.z_spin.setSuffix(" m")
        self.z_spin.setValue(current_z)

        layout = QFormLayout(self)
        layout.addRow("图层名称", self.name_edit)
        layout.addRow("基准高度", self.z_spin)
        btns = QHBoxLayout()
        for t, f in [("确定", self.accept), ("取消", self.reject)]:
            b = QPushButton(t)
            b.clicked.connect(f)
            btns.addWidget(b)
        layout.addRow(btns)

    def get_data(self) -> Tuple[str, float]:
        return self.name_edit.text().strip() or "未命名图层", self.z_spin.value()


class ShortcutSettingsDialog(QDialog):
    def __init__(self, parent: QWidget, shortcuts: Dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle("快捷键设置")
        self.resize(360, 260)
        self.editors: Dict[str, QKeySequenceEdit] = {}

        layout = QFormLayout(self)
        for key, text in {"undo": "撤销", "redo": "重做", "copy": "复制", "cut": "剪切", "paste": "粘贴"}.items():
            self.editors[key] = QKeySequenceEdit(QKeySequence(shortcuts.get(key, DEFAULT_SHORTCUTS[key])))
            layout.addRow(text, self.editors[key])

        btns = QHBoxLayout()
        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self.on_reset)
        btns.addWidget(reset_btn)
        btns.addStretch()
        for t, f in [("确定", self.accept), ("取消", self.reject)]:
            b = QPushButton(t)
            b.clicked.connect(f)
            btns.addWidget(b)
        layout.addRow(btns)

    def on_reset(self):
        for key, editor in self.editors.items():
            editor.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS[key]))

    def get_shortcuts(self) -> Dict[str, str]:
        return {k: ed.keySequence().toString(QKeySequence.SequenceFormat.NativeText).strip() or DEFAULT_SHORTCUTS[k] for k, ed in self.editors.items()}


class BaseDesignItem(QGraphicsObject):
    selected_changed, deleted, changed = Signal(object), Signal(object), Signal()

    def __init__(self, shape_data: ShapeData):
        super().__init__()
        self.shape_data = shape_data
        self.setPos(shape_data.x, shape_data.y)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.shape_data.x, self.shape_data.y = value.x(), value.y()
            self.selected_changed.emit(self)
            self.changed.emit()
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and bool(value):
            self.selected_changed.emit(self)
        return super().itemChange(change, value)

    def set_property(self, attr: str, value, update_text: bool = False):
        setattr(self.shape_data, attr, value)
        if update_text and hasattr(self, "update_text_style"): self.update_text_style()
        self.update()
        self.changed.emit()

    def set_color(self, c: QColor): self.set_property("color", c.toTuple()[:3])
    def set_base_z(self, z: float): self.set_property("base_z", z)
    def set_label(self, lbl: str): self.set_property("label", lbl, True)
    def set_font_size(self, size: float): self.set_property("font_size", max(1, size), True)

    def delete_item(self):
        self.deleted.emit(self)
        if scene := self.scene():
            scene.removeItem(self)
            if hasattr(scene, "normalize_z_orders"): scene.normalize_z_orders()

    def handle_common_context_menu(self, screen_pos, extra_actions=None, allow_reorder=True, del_text="删除图形"):
        menu, action_map, scene = QMenu(), {}, self.scene()
        main_win = getattr(scene, "main_window", None) if scene else None

        if allow_reorder:
            for t, v in {"上移一层": "up", "下移一层": "down", "置顶": "front", "置底": "back"}.items():
                action_map[menu.addAction(t)] = v
            menu.addSeparator()

        acts = {menu.addAction(t): a for t, a in {"复制": "copy", "剪切": "cut", "粘贴": "paste"}.items()}
        menu.addSeparator()

        extra_map = {menu.addAction(t): cb for t, cb in (extra_actions or {}).items()}
        if extra_actions: menu.addSeparator()

        del_act = menu.addAction(del_text)
        res = menu.exec(screen_pos)

        if res == del_act: self.delete_item()
        elif res in acts and main_win: getattr(main_win, f"{acts[res]}_selected_items" if acts[res] != "paste" else "paste_items")()
        elif res in action_map and hasattr(scene, "_reorder_item"):
            scene._reorder_item(self, action_map[res])
            self.changed.emit()
        elif res in extra_map: extra_map[res]()


class TransformableItem(BaseDesignItem):
    HANDLE_SIZE, MIN_SIZE = 0.60, 0.3

    def __init__(self, shape_data: ShapeData):
        super().__init__(shape_data)
        self.is_resizing, self.is_rotating = False, False
        self.resize_start_pos = QPointF()
        self.orig_w, self.orig_h = self._get_wh()
        self.setRotation(self.shape_data.rotation)

    def can_resize(self) -> bool: return True
    def shape_rect(self) -> QRectF: return QRectF()
    def _get_wh(self) -> Tuple[float, float]: return 1.0, 1.0
    def _set_wh(self, w, h): pass

    def get_handle_rect(self) -> QRectF:
        r = self.shape_rect()
        return QRectF(r.right() - self.HANDLE_SIZE/2, r.bottom() - self.HANDLE_SIZE/2, self.HANDLE_SIZE, self.HANDLE_SIZE)

    def get_rotate_rect(self) -> QRectF:
        r = self.shape_rect()
        return QRectF(-self.HANDLE_SIZE/2, r.top() - 1.2 - self.HANDLE_SIZE/2, self.HANDLE_SIZE, self.HANDLE_SIZE)

    def boundingRect(self) -> QRectF:
        return self.shape_rect().adjusted(-self.HANDLE_SIZE, -1.2-self.HANDLE_SIZE, self.HANDLE_SIZE, self.HANDLE_SIZE)

    def draw_handles(self, painter):
        if not self.isSelected(): return
        painter.setPen(QPen(Qt.GlobalColor.red, 0.04, Qt.PenStyle.DashLine)); painter.setBrush(Qt.BrushStyle.NoBrush)
        r = self.shape_rect(); painter.drawRect(r)
        if self.can_resize():
            painter.setBrush(QBrush(Qt.GlobalColor.white)); painter.setPen(QPen(Qt.GlobalColor.red, 0.03))
            painter.drawRect(self.get_handle_rect())
            painter.setPen(QPen(Qt.GlobalColor.blue, 0.03)); painter.drawEllipse(self.get_rotate_rect())
            painter.drawLine(QPointF(0, r.top()), QPointF(0, r.top() - 1.2))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton: 
            event.accept()
            return self.show_context_menu(to_qpoint(event.screenPos()))
        if self.can_resize() and event.button() == Qt.MouseButton.LeftButton:
            if self.get_handle_rect().contains(event.pos()):
                self.is_resizing, self.resize_start_pos = True, event.pos()
                self.orig_w, self.orig_h = self._get_wh(); self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                return event.accept()
            elif self.get_rotate_rect().contains(event.pos()):
                self.is_rotating = True; self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
                return event.accept()
        super().mousePressEvent(event); self.selected_changed.emit(self)

    def mouseMoveEvent(self, event):
        if self.is_resizing and self.can_resize():
            delta = event.pos() - self.resize_start_pos
            w, h = max(self.MIN_SIZE, self.orig_w + delta.x()), max(self.MIN_SIZE, self.orig_h + delta.y())
            if getattr(self, "is_character_shape", lambda: False)() == False and self.shape_data.shape_type in {"square", "circle", "sphere", "cone"}: w = h = max(w, h)
            self.prepareGeometryChange(); self._set_wh(w, h)
            if hasattr(self, "update_text_style"): self.update_text_style()
            self.update(); self.selected_changed.emit(self); self.changed.emit(); return event.accept()
        if self.is_rotating and self.can_resize():
            angle = math.degrees(math.atan2(event.pos().y(), event.pos().x())) + 90
            self.shape_data.rotation = angle; self.setRotation(angle); self.changed.emit(); return event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.is_resizing = self.is_rotating = False; self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        super().mouseReleaseEvent(event)


class ShapeItem(TransformableItem):
    def __init__(self, shape_data: ShapeData):
        self.qimage = None
        self.text_item = QGraphicsTextItem()
        super().__init__(shape_data)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.text_item = QGraphicsTextItem(self)
        self.text_item.setDefaultTextColor(Qt.GlobalColor.black)
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.update_text_style()
        self.load_image()

    def load_image(self):
        if self.shape_data.image_data:
            try:
                img_bytes = base64.b64decode(self.shape_data.image_data)
                self.qimage = QImage.fromData(img_bytes)
            except:
                self.qimage = None
        else:
            self.qimage = None

    def is_character_shape(self) -> bool: return self.shape_data.shape_type in CHARACTER_TYPES
    def can_resize(self) -> bool: return not self.is_character_shape()
    def get_object_id(self) -> str: return f"{self.shape_data.shape_type}:{self.shape_data.layer_index}:{id(self)}"

    def shape_rect(self) -> QRectF:
        return QRectF(-self.shape_data.width/2, -self.shape_data.height/2, self.shape_data.width, self.shape_data.height)

    def _get_wh(self) -> Tuple[float, float]: return self.shape_data.width, self.shape_data.height
    def _set_wh(self, w, h): self.shape_data.width, self.shape_data.height = w, h

    def update_text_style(self):
        font = QFont()
        font.setPointSizeF(100.0) 
        self.text_item.setFont(font)
        
        # 1pt = 0.01m，设置固定100pt基准进行缩放
        scale_factor = (max(1.0, float(self.shape_data.font_size)) * 0.01) / 100.0
        self.text_item.setScale(scale_factor)
        self.text_item.setPlainText(self.shape_data.label)
        
        br = self.text_item.boundingRect()
        self.text_item.setPos(-br.width() * scale_factor / 2, -br.height() * scale_factor / 2)

    def draw_player_icon(self, painter, rect, color):
        painter.setBrush(QBrush(color)); painter.setPen(QPen(Qt.GlobalColor.black, 0.03))
        cx, cy, w, h = rect.center().x(), rect.center().y(), rect.width(), rect.height()
        painter.drawEllipse(QPointF(cx, cy - h*0.22), min(w, h)*0.18, min(w, h)*0.18)
        painter.drawRoundedRect(QRectF(cx - w*0.14, cy - h*0.10, w*0.28, h*0.32), 0.08, 0.08)
        for sign in (-1, 1):
            painter.drawLine(QPointF(cx - sign*w*0.18, cy - h*0.02), QPointF(cx + sign*w*0.18, cy - h*0.02))
            painter.drawLine(QPointF(cx + sign*w*0.08, cy + h*0.20), QPointF(cx + sign*w*0.18, cy + h*0.40))

    def draw_enemy_icon(self, painter, rect, color):
        painter.setBrush(QBrush(color)); painter.setPen(QPen(Qt.GlobalColor.black, 0.03))
        cx, cy, w, h = rect.center().x(), rect.center().y(), rect.width(), rect.height()
        painter.drawPolygon(QPolygonF([QPointF(cx, cy - h*0.42), QPointF(cx - w*0.34, cy - h*0.10), QPointF(cx - w*0.22, cy + h*0.34), QPointF(cx + w*0.22, cy + h*0.34), QPointF(cx + w*0.34, cy - h*0.10)]))
        for sign in (-1, 1):
            painter.drawPolygon(QPolygonF([QPointF(cx + sign*w*0.12, cy - h*0.30), QPointF(cx + sign*w*0.30, cy - h*0.52), QPointF(cx + sign*w*0.04, cy - h*0.40)]))
            painter.setBrush(QBrush(Qt.GlobalColor.white))
            painter.drawEllipse(QPointF(cx + sign*w*0.10, cy - h*0.08), min(w, h)*0.05, min(w, h)*0.05)

    def draw_stair_direction_arrow(self, painter):
        r = self.shape_rect()
        sp, ep = QPointF(r.left() + r.width()*0.2, 0.0), QPointF(r.right() - r.width()*0.2, 0.0)
        if self.shape_data.stair_direction == "backward": sp, ep = ep, sp
        painter.setPen(QPen(QColor(220, 40, 40), 0.05)); painter.drawLine(sp, ep)
        sz, line = min(r.width(), r.height())*0.12, QLineF(sp, ep)
        length = max(0.001, line.length())
        ux, uy = line.dx() / length, line.dy() / length
        painter.setBrush(QBrush(QColor(220, 40, 40)))
        painter.drawPolygon(QPolygonF([ep, QPointF(ep.x() - ux*sz + uy*sz*0.6, ep.y() - uy*sz - ux*sz*0.6), QPointF(ep.x() - ux*sz - uy*sz*0.6, ep.y() - uy*sz + ux*sz*0.6)]))

    def paint(self, painter, option, widget=None):
        col, w, h, st = QColor(*self.shape_data.color), self.shape_data.width, self.shape_data.height, self.shape_data.shape_type
        painter.setRenderHint(QPainter.RenderHint.Antialiasing); painter.setBrush(QBrush(col)); painter.setPen(QPen(Qt.GlobalColor.black, 0.03))
        rect = QRectF(-min(w, h)/2, -min(w, h)/2, min(w, h), min(w, h)) if st in ("square", "circle", "sphere", "cone") else self.shape_rect()
        rw, rh = rect.width(), rect.height()

        if st in ("square", "rectangle"): painter.drawRect(rect)
        elif st in ("circle", "ellipse"): painter.drawEllipse(rect)
        elif st == "sphere":
            painter.drawEllipse(rect)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(-rw/4, -rh/2, rw/2, rh))
            painter.drawEllipse(QRectF(-rw/2, -rh/4, rw, rh/2))
        elif st == "cone":
            painter.drawEllipse(rect)
            painter.drawLine(QPointF(0, 0), QPointF(0, -rh/2))
            painter.drawLine(QPointF(0, 0), QPointF(-rw/2*0.866, rh/2*0.5))
            painter.drawLine(QPointF(0, 0), QPointF(rw/2*0.866, rh/2*0.5))
        elif st == "pyramid":
            painter.drawRect(rect)
            painter.drawLine(rect.topLeft(), rect.bottomRight())
            painter.drawLine(rect.topRight(), rect.bottomLeft())
        elif st == "triangle": painter.drawPolygon(QPolygonF([QPointF(0, -h/2), QPointF(-w/2, h/2), QPointF(w/2, h/2)]))
        elif st == "player": self.draw_player_icon(painter, rect, col)
        elif st == "enemy": self.draw_enemy_icon(painter, rect, col)
        elif st == "stair":
            painter.drawRect(rect); painter.setPen(QPen(QColor(80, 80, 80), 0.03))
            for i in range(5):
                x, y = rect.left() + i*(rect.width()/5), rect.top() + i*(rect.height()/5)
                painter.drawLine(QPointF(x, rect.bottom()), QPointF(x + rect.width()/5, y))
            self.draw_stair_direction_arrow(painter)
        elif st in ("custom_2d", "custom_3d"):
            if getattr(self, "qimage", None) and not self.qimage.isNull():
                painter.drawImage(rect, self.qimage)
            else:
                painter.drawRect(rect)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Img")

        self.draw_handles(painter)
        if self.can_resize() and self.isSelected():
            painter.setPen(QPen(Qt.GlobalColor.darkBlue, 0.03)); painter.drawText(QRectF(-w/2, -h/2 - 0.9, max(w, 10.0), 0.6), f"W:{w:.2f}m H:{h:.2f}m D:{self.shape_data.depth:.2f}m Z:{self.shape_data.base_z:.2f}m")

    def show_context_menu(self, screen_pos: QPoint): self.handle_common_context_menu(screen_pos)

    def mouseDoubleClickEvent(self, event):
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction); self.text_item.setFocus()
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event):
        self.text_item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction); self.shape_data.label = self.text_item.toPlainText()
        self.update_text_style(); self.changed.emit(); super().focusOutEvent(event)

    def set_depth(self, depth: float): self.set_property("depth", DEFAULT_CHARACTER_HEIGHT if self.is_character_shape() else max(0.1, depth))


class TextItem(TransformableItem):
    def __init__(self, shape_data: ShapeData):
        super().__init__(shape_data)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)

    def shape_rect(self) -> QRectF:
        w, h = max(1.0, self.shape_data.text_width), max(0.6, self.shape_data.text_height)
        return QRectF(-w/2, -h/2, w, h)

    def _get_wh(self) -> Tuple[float, float]: return max(1.0, self.shape_data.text_width), max(0.6, self.shape_data.text_height)
    def _set_wh(self, w, h): self.shape_data.text_width, self.shape_data.text_height = w, h

    def paint(self, painter, option, widget=None):
        r = self.shape_rect()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(90, 90, 90), 0.03, Qt.PenStyle.DashLine)); painter.setBrush(QColor(255, 255, 255, 180)); painter.drawRoundedRect(r, 0.1, 0.1)
        
        painter.save()
        font = QFont()
        font.setPointSizeF(100.0) 
        painter.setFont(font)
        painter.setPen(QPen(QColor(*self.shape_data.color)))
        
        # 1pt = 0.01m，设置固定100pt基准进行缩放
        scale_factor = (max(1.0, float(self.shape_data.font_size)) * 0.01) / 100.0
        painter.translate(r.center())
        painter.scale(scale_factor, scale_factor)
        
        text = self.shape_data.text_content or "文字"
        fm = painter.fontMetrics()
        t_rect = fm.boundingRect(text)
        painter.drawText(QRectF(-t_rect.width()/2, -t_rect.height()/2, t_rect.width(), t_rect.height()), Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

        self.draw_handles(painter)

    def show_context_menu(self, screen_pos: QPoint): self.handle_common_context_menu(screen_pos, del_text="删除文字")

    def mouseDoubleClickEvent(self, event): self.selected_changed.emit(self); super().mouseDoubleClickEvent(event)
    def set_text_content(self, text: str): self.set_property("text_content", text)


class LineItem(BaseDesignItem):
    ANCHOR_RADIUS = 0.22

    def __init__(self, shape_data: ShapeData):
        super().__init__(shape_data)
        self.setPos(0, 0)
        self.dragging_part, self.drag_last_pos, self.dragging_anchor_index = None, QPointF(), None

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and bool(value): self.selected_changed.emit(self)
        return super().itemChange(change, value)

    def get_all_points(self) -> List[QPointF]:
        pts = [(self.shape_data.x, self.shape_data.y)] + self.shape_data.control_points + [(self.shape_data.end_x, self.shape_data.end_y)]
        return [QPointF(x, y) for x, y in pts]

    def is_curve(self) -> bool: return self.shape_data.line_mode == "curve"

    def get_curve_path(self) -> QPainterPath:
        pts = self.get_all_points(); path = QPainterPath()
        if not pts: return path
        path.moveTo(pts[0])
        if len(pts) == 2: path.lineTo(pts[1]); return path
        sm_pts = [pts[0]] + pts + [pts[-1]]
        for i in range(1, len(sm_pts) - 2):
            p0, p1, p2, p3 = sm_pts[i - 1], sm_pts[i], sm_pts[i + 1], sm_pts[i + 2]
            path.cubicTo(QPointF(p1.x() + (p2.x() - p0.x()) / 6.0, p1.y() + (p2.y() - p0.y()) / 6.0), QPointF(p2.x() - (p3.x() - p1.x()) / 6.0, p2.y() - (p3.y() - p1.y()) / 6.0), p2)
        return path

    def boundingRect(self) -> QRectF:
        pad = max(0.8, self.shape_data.line_width + 0.8)
        rect = self.get_curve_path().boundingRect() if self.is_curve() else QRectF(min(self.shape_data.x, self.shape_data.end_x), min(self.shape_data.y, self.shape_data.end_y), abs(self.shape_data.end_x - self.shape_data.x), abs(self.shape_data.end_y - self.shape_data.y))
        return rect.adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        p = QPainterPath(); p.addRect(self.boundingRect()); return p

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(*self.shape_data.color), max(0.05, self.shape_data.line_width), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if self.is_curve(): painter.drawPath(self.get_curve_path())
        else: painter.drawLine(QPointF(self.shape_data.x, self.shape_data.y), QPointF(self.shape_data.end_x, self.shape_data.end_y))
        if self.isSelected():
            painter.setPen(QPen(Qt.GlobalColor.red, 0.04, Qt.PenStyle.DashLine)); painter.drawRect(self.boundingRect())
            painter.setBrush(QBrush(Qt.GlobalColor.white)); painter.setPen(QPen(Qt.GlobalColor.red, 0.03))
            for p in self.get_endpoint_handles(): painter.drawEllipse(p, 0.18, 0.18)
            if self.is_curve():
                painter.setBrush(QBrush(QColor(255, 255, 180))); painter.setPen(QPen(QColor(40, 120, 220), 0.03))
                for p in self.get_anchor_handles(): painter.drawEllipse(p, self.ANCHOR_RADIUS, self.ANCHOR_RADIUS)

    def get_endpoint_handles(self) -> List[QPointF]: return [QPointF(self.shape_data.x, self.shape_data.y), QPointF(self.shape_data.end_x, self.shape_data.end_y)]
    def get_anchor_handles(self) -> List[QPointF]: return [QPointF(x, y) for x, y in self.shape_data.control_points]

    def endpoint_at(self, pos: QPointF) -> Optional[str]:
        return next(("start" if i == 0 else "end" for i, p in enumerate(self.get_endpoint_handles()) if QLineF(pos, p).length() <= 0.35), None)

    def anchor_at(self, pos: QPointF) -> Optional[int]:
        return next((i for i, p in enumerate(self.get_anchor_handles()) if QLineF(pos, p).length() <= 0.35), None)

    def path_sample_points(self, steps: int = 60) -> List[QPointF]:
        return [self.get_curve_path().pointAtPercent(i / steps) for i in range(steps + 1)] if self.is_curve() else self.get_endpoint_handles()

    def is_near_line(self, pos: QPointF) -> bool:
        pts = self.path_sample_points()
        if len(pts) < 2: return False
        min_dist = float("inf")
        for i in range(len(pts) - 1):
            p1, p2 = pts[i], pts[i + 1]
            dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
            len_sq = dx * dx + dy * dy
            if len_sq <= 1e-6: continue
            t = max(0.0, min(1.0, ((pos.x() - p1.x()) * dx + (pos.y() - p1.y()) * dy) / len_sq))
            min_dist = min(min_dist, QLineF(pos, QPointF(p1.x() + t * dx, p1.y() + t * dy)).length())
        return min_dist <= max(0.25, self.shape_data.line_width + 0.15)

    def shape_length(self) -> float:
        pts = self.path_sample_points(90)
        return sum(QLineF(pts[i], pts[i + 1]).length() for i in range(len(pts) - 1))

    def add_anchor_at_middle(self):
        self.prepareGeometryChange(); self.shape_data.line_mode = "curve"
        self.shape_data.control_points.append(((self.shape_data.x + self.shape_data.end_x) / 2.0, (self.shape_data.y + self.shape_data.end_y) / 2.0))
        self.update(); self.changed.emit()

    def smooth_curve(self):
        if not self.shape_data.control_points: return self.add_anchor_at_middle()
        pts = self.get_all_points(); self.prepareGeometryChange()
        self.shape_data.control_points = [((pts[i-1].x() + pts[i].x()*2 + pts[i+1].x()) / 4.0, (pts[i-1].y() + pts[i].y()*2 + pts[i+1].y()) / 4.0) for i in range(1, len(pts)-1)]
        self.shape_data.line_mode = "curve"; self.update(); self.changed.emit()

    def _switch_line_mode(self):
        self.prepareGeometryChange()
        if self.is_curve(): self.shape_data.line_mode, self.shape_data.control_points = "line", []
        else:
            self.shape_data.line_mode = "curve"
            if not self.shape_data.control_points: self.shape_data.control_points = [((self.shape_data.x + self.shape_data.end_x) / 2.0, (self.shape_data.y + self.shape_data.end_y) / 2.0)]
        self.update(); self.changed.emit()

    def show_context_menu(self, screen_pos: QPoint):
        self.handle_common_context_menu(screen_pos, extra_actions={("切换为直线" if self.is_curve() else "切换为曲线"): self._switch_line_mode, "添加锚点": self.add_anchor_at_middle, "平滑曲线": self.smooth_curve}, del_text="删除线条")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton: 
            event.accept()
            return self.show_context_menu(to_qpoint(event.screenPos()))
        if event.button() == Qt.MouseButton.LeftButton:
            endpoint = self.endpoint_at(event.pos())
            anchor_idx = self.anchor_at(event.pos()) if self.is_curve() else None
            if anchor_idx is not None:
                self.dragging_part, self.dragging_anchor_index = "anchor", anchor_idx
                self.setSelected(True); self.selected_changed.emit(self); return event.accept()
            if endpoint or self.is_near_line(event.pos()):
                self.dragging_part, self.drag_last_pos = endpoint or "whole", event.pos()
                self.selected_changed.emit(self); self.setSelected(True); return event.accept()
        super().mousePressEvent(event); self.selected_changed.emit(self)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton) or not self.dragging_part: return super().mouseMoveEvent(event)
        self.prepareGeometryChange(); pos = event.pos()
        if self.dragging_part == "start": self.shape_data.x, self.shape_data.y = pos.x(), pos.y()
        elif self.dragging_part == "end": self.shape_data.end_x, self.shape_data.end_y = pos.x(), pos.y()
        elif self.dragging_part == "anchor" and self.dragging_anchor_index is not None: self.shape_data.control_points[self.dragging_anchor_index] = (pos.x(), pos.y())
        elif self.dragging_part == "whole":
            dx, dy = pos.x() - self.drag_last_pos.x(), pos.y() - self.drag_last_pos.y()
            self.shape_data.x += dx; self.shape_data.y += dy; self.shape_data.end_x += dx; self.shape_data.end_y += dy
            if self.shape_data.control_points: self.shape_data.control_points = [(x + dx, y + dy) for x, y in self.shape_data.control_points]
        self.drag_last_pos = pos; self.update(); self.selected_changed.emit(self); self.changed.emit(); event.accept()

    def mouseReleaseEvent(self, event):
        self.dragging_part = self.dragging_anchor_index = None; super().mouseReleaseEvent(event)

    def set_line_width(self, w: float):
        self.prepareGeometryChange(); self.shape_data.line_width = self.shape_data.depth = max(0.05, w)
        self.update(); self.changed.emit()


class DesignScene(QGraphicsScene):
    item_selected, item_deleted, item_changed = Signal(object), Signal(object), Signal()

    def __init__(self):
        super().__init__()
        self.setSceneRect(-1000000, -1000000, 2000000, 2000000); self.main_window = None

    def get_design_items_in_order(self) -> List[QGraphicsItem]: return sorted([it for it in self.items() if isinstance(it, (ShapeItem, LineItem, TextItem))], key=lambda item: item.zValue())

    def normalize_z_orders(self):
        for i, item in enumerate(self.get_design_items_in_order()): item.setZValue(float(i))

    def _reorder_item(self, item, action):
        items = self.get_design_items_in_order()
        if item not in items: return
        idx = items.index(item)
        if action == "up" and idx < len(items) - 1: items.insert(idx + 1, items.pop(idx))
        elif action == "down" and idx > 0: items.insert(idx - 1, items.pop(idx))
        elif action == "front": items.append(items.pop(idx))
        elif action == "back": items.insert(0, items.pop(idx))
        self.normalize_z_orders(); self.item_changed.emit()

    def add_shape(self, stype, pos, layer_index, base_z) -> ShapeItem:
        w, h = (DEFAULT_CHARACTER_WIDTH, DEFAULT_CHARACTER_HEIGHT) if stype in CHARACTER_TYPES else (1.0, 1.0)
        item = ShapeItem(ShapeData(shape_type=stype, x=pos.x(), y=pos.y(), width=w, height=h, depth=h, layer_index=layer_index, base_z=base_z))
        self._init_item(item, pos); self.item_changed.emit(); return item

    def add_text(self, pos, layer_index, base_z, content="文字备注") -> TextItem:
        item = TextItem(ShapeData(shape_type="text", x=pos.x(), y=pos.y(), color=(40, 40, 40), layer_index=layer_index, base_z=base_z, text_content=content, text_width=max(3.0, len(content) * 0.45)))
        self._init_item(item, pos); self.item_changed.emit(); return item

    def add_line(self, sp, ep, layer_index, base_z, color=(80, 80, 80), width=0.12) -> LineItem:
        item = LineItem(ShapeData(shape_type="line", x=sp.x(), y=sp.y(), end_x=ep.x(), end_y=ep.y(), line_width=max(0.05, width), color=color, layer_index=layer_index, base_z=base_z, depth=max(0.05, width)))
        self._init_item(item, None); self.item_changed.emit(); return item

    def _init_item(self, item, pos):
        if pos is not None and not isinstance(item, LineItem): item.setPos(pos)
        item.setZValue(float(len(self.get_design_items_in_order())))
        item.selected_changed.connect(self.item_selected.emit)
        item.deleted.connect(self.item_deleted.emit)
        item.changed.connect(self.item_changed.emit)
        self.addItem(item)

    def load_from_data(self, layers, shapes):
        self.clear()
        for s in shapes:
            item = LineItem(s) if s.shape_type == "line" else TextItem(s) if s.shape_type == "text" else ShapeItem(s)
            self._init_item(item, None if s.shape_type == "line" else QPointF(s.x, s.y))
        self.normalize_z_orders()

    def get_all_shapes(self) -> List[ShapeData]:
        res = []
        for it in self.get_design_items_in_order():
            if isinstance(it, (ShapeItem, TextItem)): it.shape_data.x, it.shape_data.y = it.pos().x(), it.pos().y()
            res.append(it.shape_data)
        return res

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor(248, 249, 251)); painter.setPen(QPen(QColor(220, 225, 230), 0.02))
        left, right, top, bottom = int(math.floor(rect.left())), int(math.ceil(rect.right())), int(math.floor(rect.top())), int(math.ceil(rect.bottom()))
        painter.drawLines([QLineF(x, top, x, bottom) for x in range(left, right + 1)])
        painter.drawLines([QLineF(left, y, right, y) for y in range(top, bottom + 1)])
        painter.setPen(QPen(QColor(180, 180, 180), 0.05))
        painter.drawLine(QPointF(rect.left(), 0), QPointF(rect.right(), 0)); painter.drawLine(QPointF(0, rect.top()), QPointF(0, rect.bottom()))


class ToolListWidget(QListWidget):
    def __init__(self):
        super().__init__()
        self.setDragEnabled(True); self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item: return
        st = item.data(Qt.ItemDataRole.UserRole)
        # 只允许操作用户自定义的工具
        if st in {"custom_2d", "custom_3d"}:
            menu = QMenu(self)
            rename_act = menu.addAction("重命名")
            del_act = menu.addAction("删除此工具")
            res = menu.exec(self.mapToGlobal(pos))
            if res == del_act:
                self.takeItem(self.row(item))
            elif res == rename_act:
                new_name, ok = QInputDialog.getText(self, "重命名", "请输入新名称:", text=item.text())
                if ok and new_name.strip():
                    item.setText(new_name.strip())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            item = self.currentItem()
            if item and item.data(Qt.ItemDataRole.UserRole) in {"custom_2d", "custom_3d"}:
                self.takeItem(self.row(item))
                return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions):
        if not (item := self.currentItem()) or not (st := item.data(Qt.ItemDataRole.UserRole)) or st in {"line", "select"}: return
        drag_data = {
            "type": st,
            "image_data": item.data(Qt.ItemDataRole.UserRole + 1) or "",
            "custom_3d_parts": item.data(Qt.ItemDataRole.UserRole + 2) or []
        }
        mime, drag = QMimeData(), QDrag(self)
        mime.setData(MIME_SHAPE_TYPE, json.dumps(drag_data).encode("utf-8"))
        drag.setMimeData(mime); drag.exec(Qt.DropAction.CopyAction)


class MiniMapView(QGraphicsView):
    def __init__(self, scene, main_view):
        super().__init__(scene, main_view)
        self.main_view = main_view
        self.setRenderHints(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background: rgba(245, 245, 245, 200); border: 1px solid #aaa; border-radius: 4px;")
        self.setInteractive(False)
        main_view.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        main_view.verticalScrollBar().valueChanged.connect(self.viewport().update)

    def mousePressEvent(self, event): self._nav(event)
    def mouseMoveEvent(self, event): self._nav(event)
    def _nav(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.main_view.centerOn(self.mapToScene(event.pos()))

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self.viewport()); p.setPen(QPen(Qt.GlobalColor.red, 2)); p.setBrush(QColor(255, 0, 0, 30))
        mr = self.main_view.mapToScene(self.main_view.viewport().rect()).boundingRect()
        p.drawRect(self.mapFromScene(mr).boundingRect())


class DesignView(QGraphicsView):
    def __init__(self, scene, main_window):
        super().__init__(scene)
        self.design_scene, self.main_window = scene, main_window
        self.is_drawing_line, self.temp_line_item, self.is_panning = False, None, False
        self.pan_start_pos, self.pan_start_h_value, self.pan_start_v_value = QPoint(), 0, 0
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate); self.setAcceptDrops(True); self.scale(25, 25)
        self.minimap = MiniMapView(scene, self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        mw, mh = 200, 150
        self.minimap.setGeometry(self.width() - mw - 20, self.height() - mh - 20, mw, mh)
        br = self.design_scene.itemsBoundingRect()
        if br.width() <= 0 or br.height() <= 0: br = QRectF(-30, -30, 60, 60)
        else: br = br.united(QRectF(-30, -30, 60, 60))
        self.minimap.fitInView(br, Qt.AspectRatioMode.KeepAspectRatio)

    def dragEnterEvent(self, event): event.acceptProposedAction() if event.mimeData().hasFormat(MIME_SHAPE_TYPE) else super().dragEnterEvent(event)
    def dragMoveEvent(self, event): event.acceptProposedAction() if event.mimeData().hasFormat(MIME_SHAPE_TYPE) else super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(MIME_SHAPE_TYPE): return event.ignore()
        try:
            st_data = bytes(event.mimeData().data(MIME_SHAPE_TYPE)).decode("utf-8").strip()
            if st_data.startswith("{"):
                drag_data = json.loads(st_data)
                st = drag_data.get("type", "")
                img_data = drag_data.get("image_data", "")
                parts = drag_data.get("custom_3d_parts", [])
            else:
                st = st_data
                img_data = ""
                parts = []
        except: return event.ignore()
        
        if not st: return event.ignore()

        pos, layer_idx = self.mapToScene(to_qpoint(event.position())), self.main_window.get_current_layer_index()
        if st == "text":
            item = self.design_scene.add_text(pos, layer_idx, 0.0)
        else:
            item = self.design_scene.add_shape(st, pos, layer_idx, 0.0)
            item.shape_data.image_data = img_data
            item.shape_data.custom_3d_parts = parts
            if img_data: item.load_image()
            
        item.setSelected(True); self.design_scene.item_selected.emit(item); self.main_window.push_history_snapshot("drop_create"); event.acceptProposedAction()

    def mousePressEvent(self, event):
        tool = self.main_window.get_current_tool_type()
        if event.button() == Qt.MouseButton.MiddleButton:
            self.is_panning, self.pan_start_pos, self.pan_start_h_value, self.pan_start_v_value = True, event.pos(), self.horizontalScrollBar().value(), self.verticalScrollBar().value()
            self.setCursor(Qt.CursorShape.ClosedHandCursor); return event.accept()
        if tool == "line" and event.button() == Qt.MouseButton.LeftButton:
            start_pos = self.mapToScene(event.pos())
            if isinstance(self.design_scene.itemAt(start_pos, self.transform()), BaseDesignItem): return super().mousePressEvent(event)
            self.is_drawing_line = True
            self.temp_line_item = self.design_scene.add_line(start_pos, start_pos, self.main_window.get_current_layer_index(), 0.0, self.main_window.get_default_line_color(), self.main_window.get_default_line_width())
            self.temp_line_item.setSelected(True); self.design_scene.item_selected.emit(self.temp_line_item); return event.accept()
        if event.button() == Qt.MouseButton.RightButton:
            it = self.design_scene.itemAt(self.mapToScene(event.pos()), self.transform())
            while it and not isinstance(it, BaseDesignItem):
                it = it.parentItem()
            if isinstance(it, BaseDesignItem):
                it.show_context_menu(self.mapToGlobal(event.pos()))
            else:
                self.main_window.show_empty_context_menu(self.mapToGlobal(event.pos()))
            return event.accept()
        super().mousePressEvent(event)
        if tool == "select" and event.button() == Qt.MouseButton.LeftButton and not self.design_scene.itemAt(self.mapToScene(event.pos()), self.transform()):
            self.design_scene.clearSelection(); self.main_window.clear_properties()

    def mouseMoveEvent(self, event):
        if self.is_panning:
            delta = event.pos() - self.pan_start_pos
            self.horizontalScrollBar().setValue(self.pan_start_h_value - delta.x()); self.verticalScrollBar().setValue(self.pan_start_v_value - delta.y()); return event.accept()
        if self.is_drawing_line and self.temp_line_item:
            pos = self.mapToScene(event.pos())
            self.temp_line_item.prepareGeometryChange(); self.temp_line_item.shape_data.end_x, self.temp_line_item.shape_data.end_y = pos.x(), pos.y()
            self.temp_line_item.update(); self.design_scene.item_selected.emit(self.temp_line_item); self.design_scene.item_changed.emit(); return event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self.is_panning:
            self.is_panning = False; self.setCursor(Qt.CursorShape.ArrowCursor); return event.accept()
        if self.is_drawing_line and event.button() == Qt.MouseButton.LeftButton:
            if self.temp_line_item:
                if self.temp_line_item.shape_length() < 0.2: self.design_scene.removeItem(self.temp_line_item)
                else:
                    self.temp_line_item.setSelected(True); self.design_scene.item_selected.emit(self.temp_line_item); self.design_scene.normalize_z_orders(); self.design_scene.item_changed.emit(); self.main_window.push_history_snapshot("draw_line")
            self.is_drawing_line, self.temp_line_item = False, None
            return event.accept()
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        zoom = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(zoom, zoom)


class OpenGLLevelView(QOpenGLWidget):
    PREVIEW_WIDTH, PREVIEW_HEIGHT = 1920, 1080

    def __init__(self, shapes, layers, show_labels=True):
        super().__init__()
        self.shapes, self.layers, self.show_labels = shapes, layers, show_labels
        self.perspective_enabled, self.wireframe_enabled, self._gl_initialized = True, False, False
        self.camera_x, self.camera_y, self.camera_z = 0.0, 8.0, 16.0
        self.yaw, self.pitch = -90.0, -25.0
        self.last_mouse_pos, self.is_rotating, self.keys_pressed = None, False, set()
        self.textures = {}
        self.timer = QTimer(self); self.timer.timeout.connect(self.update_camera); self.timer.start(16)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus); self.setMouseTracking(True)

    def get_layer_z_offset(self, idx: int) -> float: return self.layers[idx].z_offset if 0 <= idx < len(self.layers) else 0.0
    
    def get_real_base_z(self, shape) -> float:
        return get_real_base_z_with_adjustment(shape, self.shapes, self.layers)

    def set_show_labels(self, visible): self.show_labels = visible; self.update()
    def set_wireframe_enabled(self, enabled): self.wireframe_enabled = enabled; self.update()
    def set_perspective_enabled(self, enabled):
        self.perspective_enabled = enabled
        if self._gl_initialized:
            self.makeCurrent(); self.resizeGL(self.width(), self.height()); self.doneCurrent(); self.update()

    def reset_camera(self):
        self.camera_x, self.camera_y, self.camera_z = 0.0, 8.0, 16.0
        self.yaw, self.pitch = -90.0, -25.0
        self.update()

    def export_current_view_image(self, file_path, w=PREVIEW_WIDTH, h=PREVIEW_HEIGHT):
        if not file_path: return False
        orig_size, parent = self.size(), self.parentWidget()
        try:
            self.makeCurrent()
            if parent: parent.setUpdatesEnabled(False)
            self.resize(w, h); self.update(); self.repaint(); QApplication.processEvents(); self.makeCurrent()
            img = self.grabFramebuffer()
            if img.isNull(): return False
            if img.width() != w or img.height() != h: img = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            return img.save(file_path, "PNG" if file_path.lower().endswith(".png") else "JPG")
        finally:
            self.resize(orig_size); self.update()
            if parent: parent.setUpdatesEnabled(True)

    def initializeGL(self):
        glClearColor(0.92, 0.95, 0.98, 1.0); glEnable(GL_DEPTH_TEST); glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA); glDisable(GL_CULL_FACE)
        self._gl_initialized = True

    def resizeGL(self, width, height):
        if not self._gl_initialized: return
        glViewport(0, 0, width, max(1, height)); glMatrixMode(GL_PROJECTION); glLoadIdentity()
        aspect = width / max(1, height)
        if self.perspective_enabled: gluPerspective(60.0, aspect, 0.1, 1000.0)
        else: glOrtho(-18.0 * aspect, 18.0 * aspect, -18.0, 18.0, -1000.0, 1000.0)
        glMatrixMode(GL_MODELVIEW); glLoadIdentity()

    def get_texture(self, shape):
        if not getattr(shape, "image_data", ""): return 0
        if id(shape) in self.textures: return self.textures[id(shape)]
        try:
            img_bytes = base64.b64decode(shape.image_data)
            qimg = QImage.fromData(img_bytes).convertToFormat(QImage.Format.Format_RGBA8888).mirrored()
            tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, qimg.width(), qimg.height(), 0, GL_RGBA, GL_UNSIGNED_BYTE, qimg.bits().tobytes())
            self.textures[id(shape)] = tex
            return tex
        except: return 0

    def paintGL(self):
        glEnable(GL_DEPTH_TEST)
        glDepthMask(GL_TRUE)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_CULL_FACE)

        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT); glMatrixMode(GL_MODELVIEW); glLoadIdentity()
        self.apply_camera(); self.draw_ground_grid()
        sorted_shapes = sorted(self.shapes, key=self.get_real_base_z)
        for s in sorted_shapes: self.draw_shape_3d(s)
        
        if not self.show_labels: return
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        for s in sorted_shapes: self.draw_shape_label_2d(painter, s); self.draw_shape_dimension_2d(painter, s)
        painter.end()

    def get_camera_vector(self):
        ry, rp = math.radians(self.yaw), math.radians(self.pitch)
        cp = math.cos(rp)
        return math.cos(ry) * cp, math.sin(rp), math.sin(ry) * cp

    def apply_camera(self):
        fx, fy, fz = self.get_camera_vector()
        gluLookAt(self.camera_x, self.camera_y, self.camera_z, self.camera_x + fx, self.camera_y + fy, self.camera_z + fz, 0.0, 1.0, 0.0)

    def get_camera_basis(self):
        fx, fy, fz = self.get_camera_vector(); fl = math.hypot(fx, fy, fz)
        f = (fx/fl, fy/fl, fz/fl) if fl else (0.0, 0.0, -1.0)
        rx, ry, rz = -f[2], 0.0, f[0]; rl = math.hypot(rx, ry, rz)
        r = (rx/rl, ry/rl, rz/rl) if rl else (1.0, 0.0, 0.0)
        u = (r[1]*f[2]-r[2]*f[1], r[2]*f[0]-r[0]*f[2], r[0]*f[1]-r[1]*f[0])
        return f, r, u

    def world_to_screen(self, wx, wy, wz):
        f, r, u = self.get_camera_basis(); dx, dy, dz = wx - self.camera_x, wy - self.camera_y, wz - self.camera_z
        cx, cy, cz = sum(d*v for d, v in zip((dx, dy, dz), r)), sum(d*v for d, v in zip((dx, dy, dz), u)), sum(d*v for d, v in zip((dx, dy, dz), f))
        aspect = self.width() / max(1, self.height())
        if self.perspective_enabled:
            if cz <= 0.1: return None
            nx, ny = cx / (cz * math.tan(math.radians(30.0)) * aspect), cy / (cz * math.tan(math.radians(30.0)))
        else: nx, ny = cx / (18.0 * aspect), cy / 18.0
        return QPointF((nx + 1.0) * 0.5 * self.width(), (1.0 - ny) * 0.5 * self.height()) if -1.2 <= nx <= 1.2 and -1.2 <= ny <= 1.2 else None

    def draw_shape_label_2d(self, painter, shape):
        lbl = shape.text_content.strip() if shape.shape_type == "text" else shape.label.strip()
        if not lbl: return
        bz = self.get_real_base_z(shape)
        if shape.shape_type == "line": wx, wy, wz = (shape.x + shape.end_x)/2, bz + 0.15, (shape.y + shape.end_y)/2
        else: wx, wy, wz = shape.x, bz + max(shape.depth, 0.2) + 0.25, shape.y
        if not (pos := self.world_to_screen(wx, wy, wz)): return
        painter.setFont(QFont("", max(8, int(shape.font_size)))); trect = painter.fontMetrics().boundingRect(lbl)
        r = QRectF(pos.x() - trect.width()/2 - 6, pos.y() - trect.height() - 14, trect.width() + 12, trect.height() + 8)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(255, 255, 255, 210)); painter.drawRoundedRect(r, 6, 6)
        painter.setPen(QPen(QColor(*shape.color))); painter.drawText(r.adjusted(6, 4, -6, -4), Qt.AlignmentFlag.AlignCenter, lbl)

    def draw_shape_dimension_2d(self, painter, shape):
        if shape.shape_type == "text": return
        bz = self.get_real_base_z(shape)
        if shape.shape_type == "line":
            t = f"{'曲线' if shape.line_mode == 'curve' else '直线'} L {self.get_line_length(shape):.2f}m / T {shape.line_width:.2f}m"
            wx, wy, wz = (shape.x + shape.end_x)/2, bz + 0.15, (shape.y + shape.end_y)/2
        else:
            t = f"Φ {min(shape.width, shape.height):.2f}m / H {shape.depth:.2f}m" if shape.shape_type in ("circle", "sphere", "cone") else f"W {shape.width:.2f}m / D {shape.height:.2f}m / H {shape.depth:.2f}m"
            wx, wy, wz = shape.x, bz + max(shape.depth * 0.55, 0.2), shape.y
        if not (pos := self.world_to_screen(wx, wy, wz)) or not t: return
        painter.setFont(QFont("", 10)); trect = painter.fontMetrics().boundingRect(t)
        r = QRectF(pos.x() - trect.width()/2 - 8, pos.y() - trect.height()/2, trect.width() + 16, trect.height() + 8)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(20, 20, 20, 165)); painter.drawRoundedRect(r, 5, 5)
        painter.setPen(QPen(QColor(255, 255, 255))); painter.drawText(r, Qt.AlignmentFlag.AlignCenter, t)

    def draw_ground_grid(self):
        glColor4f(0.75, 0.78, 0.82, 1.0); glBegin(GL_LINES)
        cx, cz = int(math.floor(self.camera_x)), int(math.floor(self.camera_z))
        r = 100
        for i in range(cx - r, cx + r + 1):
            glVertex3f(i, 0, cz - r); glVertex3f(i, 0, cz + r)
        for i in range(cz - r, cz + r + 1):
            glVertex3f(cx - r, 0, i); glVertex3f(cx + r, 0, i)
        glEnd()

    def get_line_sample_points(self, shape, steps: int = 60) -> List[Tuple[float, float]]:
        pts = [(shape.x, shape.y)] + (shape.control_points or []) + [(shape.end_x, shape.end_y)]
        if shape.line_mode != "curve" or len(pts) <= 2: return pts
        path, qpts = QPainterPath(), [QPointF(x, y) for x, y in pts]
        path.moveTo(qpts[0]); sm_pts = [qpts[0]] + qpts + [qpts[-1]]
        for i in range(1, len(sm_pts) - 2):
            p0, p1, p2, p3 = sm_pts[i - 1], sm_pts[i], sm_pts[i + 1], sm_pts[i + 2]
            path.cubicTo(QPointF(p1.x() + (p2.x() - p0.x()) / 6.0, p1.y() + (p2.y() - p0.y()) / 6.0), QPointF(p2.x() - (p3.x() - p1.x()) / 6.0, p2.y() - (p3.y() - p1.y()) / 6.0), p2)
        return [(path.pointAtPercent(i / steps).x(), path.pointAtPercent(i / steps).y()) for i in range(steps + 1)]

    def get_line_length(self, shape) -> float:
        pts = self.get_line_sample_points(shape, 100)
        return sum(math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1]) for i in range(len(pts)-1))

    def _render_gl_shape(self, r, g, b, draw_faces, draw_edges):
        glColor3f(r, g, b)
        if self.wireframe_enabled:
            glPolygonMode(GL_FRONT_AND_BACK, GL_LINE); draw_faces(); glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
        else:
            draw_faces(); glColor3f(0.12, 0.12, 0.12); glBegin(GL_LINES); draw_edges(); glEnd()

    def draw_sphere(self, cx, cy, cz, rx, ry, rz, r, g, b, slices=24, stacks=16):
        glPushMatrix(); glTranslatef(cx, cy, cz)
        try:
            def _f():
                for i in range(stacks):
                    lat0 = math.pi * (-0.5 + float(i) / stacks)
                    y0 = math.sin(lat0); yr0 = math.cos(lat0)
                    lat1 = math.pi * (-0.5 + float(i+1) / stacks)
                    y1 = math.sin(lat1); yr1 = math.cos(lat1)
                    glBegin(GL_QUAD_STRIP)
                    for j in range(slices + 1):
                        lng = 2 * math.pi * float(j) / slices
                        x = math.cos(lng); z = math.sin(lng)
                        glVertex3f(x * yr0 * rx, y0 * ry, z * yr0 * rz)
                        glVertex3f(x * yr1 * rx, y1 * ry, z * yr1 * rz)
                    glEnd()
            def _e():
                # 修复: 移除不兼容且导致异常冲突的 glBegin，仅输出配对的线段顶点
                for i in range(1, stacks):
                    lat = math.pi * (-0.5 + float(i) / stacks)
                    y = math.sin(lat); yr = math.cos(lat)
                    for j in range(slices):
                        lng1 = 2 * math.pi * float(j) / slices
                        lng2 = 2 * math.pi * float(j + 1) / slices
                        glVertex3f(math.cos(lng1) * yr * rx, y * ry, math.sin(lng1) * yr * rz)
                        glVertex3f(math.cos(lng2) * yr * rx, y * ry, math.sin(lng2) * yr * rz)
                for j in range(slices):
                    lng = 2 * math.pi * float(j) / slices
                    x = math.cos(lng); z = math.sin(lng)
                    for i in range(stacks):
                        lat1 = math.pi * (-0.5 + float(i) / stacks)
                        lat2 = math.pi * (-0.5 + float(i + 1) / stacks)
                        glVertex3f(x * math.cos(lat1) * rx, math.sin(lat1) * ry, z * math.cos(lat1) * rz)
                        glVertex3f(x * math.cos(lat2) * rx, math.sin(lat2) * ry, z * math.cos(lat2) * rz)
            self._render_gl_shape(r, g, b, _f, _e)
        finally:
            glPopMatrix()

    def draw_cone(self, cx, cy, cz, rx, rz, h, r, g, b, segments=36):
        hh = h / 2
        glPushMatrix(); glTranslatef(cx, cy, cz)
        try:
            pts = [(math.cos(2*math.pi*i/segments)*rx, math.sin(2*math.pi*i/segments)*rz) for i in range(segments)]
            def _f():
                glBegin(GL_TRIANGLES)
                for i in range(segments):
                    p1, p2 = pts[i], pts[(i+1)%segments]
                    glVertex3f(p1[0], -hh, p1[1]); glVertex3f(p2[0], -hh, p2[1]); glVertex3f(0, hh, 0)
                glEnd()
                glBegin(GL_POLYGON)
                for p in pts: glVertex3f(p[0], -hh, p[1])
                glEnd()
            def _e():
                for i in range(segments):
                    p, np = pts[i], pts[(i+1)%segments]
                    glVertex3f(p[0], -hh, p[1]); glVertex3f(0, hh, 0)
                    glVertex3f(p[0], -hh, p[1]); glVertex3f(np[0], -hh, np[1])
            self._render_gl_shape(r, g, b, _f, _e)
        finally:
            glPopMatrix()

    def draw_pyramid(self, cx, cy, cz, w, h, d, r, g, b):
        x, y, z = w/2, h/2, d/2
        glPushMatrix(); glTranslatef(cx, cy, cz)
        try:
            vs = [(-x,-y,z), (x,-y,z), (x,-y,-z), (-x,-y,-z), (0,y,0)]
            def _f():
                glBegin(GL_QUADS)
                for i in (3, 2, 1, 0): glVertex3f(*vs[i])
                glEnd()
                glBegin(GL_TRIANGLES)
                for f in [(0,1,4), (1,2,4), (2,3,4), (3,0,4)]:
                    for i in f: glVertex3f(*vs[i])
                glEnd()
            def _e():
                for e in [(0,1), (1,2), (2,3), (3,0), (0,4), (1,4), (2,4), (3,4)]:
                    glVertex3f(*vs[e[0]]); glVertex3f(*vs[e[1]])
            self._render_gl_shape(r, g, b, _f, _e)
        finally:
            glPopMatrix()

    def draw_shape_3d(self, shape):
        if shape.shape_type == "line":
            glPushMatrix()
            try: self.draw_line_3d(shape)
            finally: glPopMatrix()
            return

        glPushMatrix()
        try:
            glTranslatef(shape.x, 0, shape.y)
            glRotatef(-shape.rotation, 0, 1, 0)

            r, g, b = [v / 255.0 for v in shape.color]
            bz = self.get_real_base_z(shape)
            cy, st = bz + shape.depth / 2, shape.shape_type
            
            if st in {"rectangle", "square"}: self.draw_box(0, cy, 0, shape.width, shape.depth, shape.height, r, g, b)
            elif st == "circle": rad = min(shape.width, shape.height)/2; self.draw_cylinder(0, cy, 0, rad, rad, shape.depth, r, g, b)
            elif st == "ellipse": self.draw_cylinder(0, cy, 0, shape.width/2, shape.height/2, shape.depth, r, g, b)
            elif st == "sphere": rad = min(shape.width, shape.height)/2; self.draw_sphere(0, cy, 0, rad, shape.depth/2, rad, r, g, b)
            elif st == "cone": rad = min(shape.width, shape.height)/2; self.draw_cone(0, cy, 0, rad, rad, shape.depth, r, g, b)
            elif st == "pyramid": self.draw_pyramid(0, cy, 0, shape.width, shape.depth, shape.height, r, g, b)
            elif st == "triangle": self.draw_triangular_prism(0, cy, 0, shape.width, shape.height, shape.depth, r, g, b)
            elif st == "player": self.draw_player_model(shape)
            elif st == "enemy": self.draw_enemy_model(shape)
            elif st == "stair": self.draw_stair_3d(shape)
            elif st == "custom_2d": self.draw_custom_2d(shape)
            elif st == "custom_3d": self.draw_custom_3d(shape)
        finally:
            glPopMatrix()

    def draw_custom_2d(self, shape):
        r, g, b = [v / 255.0 for v in shape.color]
        cy = self.get_real_base_z(shape)
        tex = self.get_texture(shape)
        glPushMatrix()
        try:
            glTranslatef(0, cy, 0)
            w, h_ = shape.width/2, shape.height/2
            if tex:
                self.draw_box(0, shape.depth/2, 0, shape.width, shape.depth, shape.height, r, g, b)
                glEnable(GL_TEXTURE_2D)
                glBindTexture(GL_TEXTURE_2D, tex)
                glColor3f(1.0, 1.0, 1.0)
                glBegin(GL_QUADS)
                glTexCoord2f(0, 0); glVertex3f(-w, shape.depth + 0.001, -h_)
                glTexCoord2f(1, 0); glVertex3f(w, shape.depth + 0.001, -h_)
                glTexCoord2f(1, 1); glVertex3f(w, shape.depth + 0.001, h_)
                glTexCoord2f(0, 1); glVertex3f(-w, shape.depth + 0.001, h_)
                glEnd()
                glDisable(GL_TEXTURE_2D)
            else:
                self.draw_box(0, shape.depth/2, 0, shape.width, shape.depth, shape.height, r, g, b)
        finally:
            glPopMatrix()

    def draw_custom_3d(self, shape):
        glPushMatrix()
        try:
            cy = self.get_real_base_z(shape)
            glTranslatef(0, cy, 0)
            for part in getattr(shape, "custom_3d_parts", []):
                pt = part.get("type", "box")
                px, py, pz = part.get("x", 0.0), part.get("y", 0.0), part.get("z", 0.0)
                pw, ph, pd = part.get("w", 1.0), part.get("h", 1.0), part.get("d", 1.0)
                pr, pg, pb = [v/255.0 for v in part.get("color", (200, 200, 200))]
                if pt == "box":
                    self.draw_box(px, py + ph/2, pz, pw, ph, pd, pr, pg, pb)
                elif pt == "cylinder":
                    self.draw_cylinder(px, py + ph/2, pz, pw/2, pd/2, ph, pr, pg, pb)
                elif pt == "sphere":
                    self.draw_sphere(px, py + ph/2, pz, pw/2, ph/2, pd/2, pr, pg, pb)
                elif pt == "cone":
                    self.draw_cone(px, py + ph/2, pz, pw/2, pd/2, ph, pr, pg, pb)
                elif pt == "pyramid":
                    self.draw_pyramid(px, py + ph/2, pz, pw, ph, pd, pr, pg, pb)
        finally:
            glPopMatrix()

    def draw_line_3d(self, shape):
        glColor3f(*[v / 255.0 for v in shape.color]); glLineWidth(max(1.0, shape.line_width * 10.0)); glBegin(GL_LINE_STRIP)
        bz = self.get_real_base_z(shape)
        for x, y in self.get_line_sample_points(shape, 80): glVertex3f(x, bz, y)
        glEnd(); glLineWidth(1.0)

    def get_platform_binding_key(self, shape) -> str: return f"{shape.shape_type}|{shape.layer_index}|{shape.x:.3f}|{shape.y:.3f}|{shape.width:.3f}|{shape.height:.3f}|{shape.base_z + self.get_layer_z_offset(shape.layer_index):.3f}"

    def find_connected_platforms(self, stair):
        exp_s = exp_e = None
        for s in self.shapes:
            if s is stair or s.shape_type not in PLATFORM_TYPES: continue
            k = self.get_platform_binding_key(s)
            if stair.stair_start_binding == k: exp_s = s
            if stair.stair_end_binding == k: exp_e = s
        if exp_s and exp_e: return [exp_s, exp_e]
        cands = sorted([s for s in self.shapes if s is not stair and s.shape_type in PLATFORM_TYPES and check_point_inside_platform(s, stair.x, stair.y)], key=lambda x: x.base_z + self.get_layer_z_offset(x.layer_index))
        uh = []
        for c in cands:
            if all(abs((c.base_z + self.get_layer_z_offset(c.layer_index)) - (e.base_z + self.get_layer_z_offset(e.layer_index))) > 0.01 for e in uh): uh.append(c)
        return uh[:2]

    def draw_stair_3d(self, stair):
        conn = self.find_connected_platforms(stair)
        sz = stair.base_z + self.get_layer_z_offset(stair.layer_index)
        
        if len(conn) >= 2:
            pz0 = conn[0].base_z + self.get_layer_z_offset(conn[0].layer_index) + conn[0].depth
            pz1 = conn[1].base_z + self.get_layer_z_offset(conn[1].layer_index) + conn[1].depth
        else:
            pz0 = sz
            pz1 = sz + stair.depth

        is_auto = getattr(stair, 'stair_auto_height', True)
        if is_auto:
            start_z, end_z = pz0, pz1
        else:
            start_z = pz0
            end_z = pz0 + stair.depth * (1.0 if pz1 >= pz0 else -1.0)

        if stair.stair_direction == "backward": start_z, end_z = end_z, start_z
        sign = 1.0 if end_z >= start_z else -1.0
        rise = max(0.1, abs(end_z - start_z)); steps = max(3, int(rise / 0.3))
        r, g, b = [v / 255.0 for v in stair.color]
        sh, sw = rise / steps, stair.width / steps
        for i in range(steps):
            cx = ((-stair.width/2 + sw/2 + i*sw) if stair.stair_direction != "backward" else (stair.width/2 - sw/2 - i*sw))
            self.draw_box(cx, start_z + sign*(sh*(i+1))/2, 0, sw, sh*(i+1), max(0.5, stair.height), r, g, b)

    def draw_box(self, cx, cy, cz, w, h, d, r, g, b):
        x, y, z = w/2, h/2, d/2
        glPushMatrix(); glTranslatef(cx, cy, cz)
        try:
            vs = [(-x,-y,z), (x,-y,z), (x,y,z), (-x,y,z), (-x,-y,-z), (x,-y,-z), (x,y,-z), (-x,y,-z)]
            def _f():
                glBegin(GL_QUADS)
                for f in BOX_FACES:
                    for i in f: glVertex3f(*vs[i])
                glEnd()
            def _e():
                for e in BOX_EDGES: glVertex3f(*vs[e[0]]); glVertex3f(*vs[e[1]])
            self._render_gl_shape(r, g, b, _f, _e)
        finally:
            glPopMatrix()

    def draw_cylinder(self, cx, cy, cz, rx, rz, h, r, g, b, segments=36):
        hh = h / 2
        glPushMatrix(); glTranslatef(cx, cy, cz)
        try:
            pts = [(math.cos(2*math.pi*i/segments)*rx, math.sin(2*math.pi*i/segments)*rz) for i in range(segments)]
            def _f():
                glBegin(GL_QUADS)
                for i in range(segments):
                    p1, p2 = pts[i], pts[(i+1)%segments]
                    glVertex3f(p1[0], -hh, p1[1]); glVertex3f(p2[0], -hh, p2[1]); glVertex3f(p2[0], hh, p2[1]); glVertex3f(p1[0], hh, p1[1])
                glEnd()
                for y in (hh, -hh):
                    glBegin(GL_POLYGON)
                    for p in pts: glVertex3f(p[0], y, p[1])
                    glEnd()
            def _e():
                for i in range(segments):
                    for p in (pts[i], pts[(i+1)%segments]): glVertex3f(p[0], -hh, p[1]); glVertex3f(p[0], hh, p[1])
            self._render_gl_shape(r, g, b, _f, _e)
        finally:
            glPopMatrix()

    def draw_triangular_prism(self, cx, cy, cz, w, d, h, r, g, b):
        hh = h / 2; p1, p2, p3 = (0.0, d/2), (-w/2, -d/2), (w/2, -d/2)
        vs = [(p1[0], -hh, p1[1]), (p2[0], -hh, p2[1]), (p3[0], -hh, p3[1]), (p1[0], hh, p1[1]), (p2[0], hh, p2[1]), (p3[0], hh, p3[1])]
        glPushMatrix(); glTranslatef(cx, cy, cz)
        try:
            def _f():
                glBegin(GL_TRIANGLES)
                for i in (0, 1, 2, 3, 5, 4): glVertex3f(*vs[i])
                glEnd()
                glBegin(GL_QUADS)
                for f in [(0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)]:
                    for i in f: glVertex3f(*vs[i])
                glEnd()
            def _e():
                for e in [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5)]: glVertex3f(*vs[e[0]]); glVertex3f(*vs[e[1]])
            self._render_gl_shape(r, g, b, _f, _e)
        finally:
            glPopMatrix()

    def draw_player_model(self, shape):
        r, g, b = [v / 255.0 for v in shape.color]; y = self.get_real_base_z(shape); h = max(0.3, shape.depth)
        bh, lh, hh = h*0.42, h*0.38, h*0.20; bw, bd = max(0.18, shape.width*0.34), max(0.18, shape.height*0.22)
        lw, ld = max(0.08, shape.width*0.12), max(0.08, shape.height*0.14); hr = max(0.10, min(shape.width, shape.height)*0.16)
        self.draw_box(0, y + lh/2, - lw*0.65, lw, lh, ld, r, g, b)
        self.draw_box(0, y + lh/2, lw*0.65, lw, lh, ld, r, g, b)
        self.draw_box(0, y + lh + bh/2, 0, bw, bh, bd, r, g, b)
        self.draw_cylinder(0, y + lh + bh + hh/2, 0, hr, hr, hh, r, g, b, 24)

    def draw_enemy_model(self, shape):
        r, g, b = [v / 255.0 for v in shape.color]; y = self.get_real_base_z(shape); h = max(0.3, shape.depth)
        lw, lh, ld, lo = max(0.08, shape.width*0.14), h*0.22, max(0.08, shape.height*0.14), max(0.12, shape.height*0.16)
        bw, bh, bd = max(0.30, shape.width*0.52), h*0.50, max(0.24, shape.height*0.40)
        hw, hh, hd = max(0.22, shape.width*0.34), h*0.18, max(0.18, shape.height*0.26)
        nw, nh, nd = max(0.08, shape.width*0.10), h*0.10, max(0.12, shape.height*0.12)
        self.draw_box(- shape.width*0.10, y + lh/2, - lo, lw, lh, ld, r, g, b)
        self.draw_box(shape.width*0.10, y + lh/2, lo, lw, lh, ld, r, g, b)
        self.draw_box(0, y + lh + bh/2, 0, bw, bh, bd, r, g, b)
        self.draw_box(0, y + lh + bh + hh/2, 0, hw, hh, hd, r, g, b)
        self.draw_triangular_prism(- hw*0.22, y + lh + bh + hh + nh/2, 0, nw, nd, nh, r, g, b)
        self.draw_triangular_prism(hw*0.22, y + lh + bh + hh + nh/2, 0, nw, nd, nh, r, g, b)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0: return super().wheelEvent(event)
        speed = 1.5 if delta > 0 else -1.5
        fx, fy, fz = self.get_camera_vector()
        self.camera_x += fx * speed; self.camera_y += fy * speed; self.camera_z += fz * speed
        self.update(); event.accept()

    def keyPressEvent(self, event): self.keys_pressed.add(event.key()); super().keyPressEvent(event)
    def keyReleaseEvent(self, event): self.keys_pressed.discard(event.key()); super().keyReleaseEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.is_rotating, self.last_mouse_pos = True, event.position()
            return event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.is_rotating: return super().mouseMoveEvent(event)
        if self.last_mouse_pos:
            self.yaw += (event.position().x() - self.last_mouse_pos.x()) * 0.15
            self.pitch = max(-89.0, min(89.0, self.pitch - (event.position().y() - self.last_mouse_pos.y()) * 0.15))
            self.update()
        self.last_mouse_pos, _ = event.position(), event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self.is_rotating, self.last_mouse_pos = False, None
            return event.accept()
        super().mouseReleaseEvent(event)

    def update_camera(self):
        fx, fy, fz = self.get_camera_vector()
        length = math.hypot(fx, fz)
        if not length: return
        fx, fz, sp = fx / length, fz / length, 0.18
        if Qt.Key.Key_W in self.keys_pressed: self.camera_x += fx * sp; self.camera_z += fz * sp
        if Qt.Key.Key_S in self.keys_pressed: self.camera_x -= fx * sp; self.camera_z -= fz * sp
        if Qt.Key.Key_A in self.keys_pressed: self.camera_x -= -fz * sp; self.camera_z -= fx * sp
        if Qt.Key.Key_D in self.keys_pressed: self.camera_x += -fz * sp; self.camera_z += fx * sp
        if Qt.Key.Key_Q in self.keys_pressed: self.camera_y += sp
        if Qt.Key.Key_E in self.keys_pressed: self.camera_y -= sp
        self.update()


class Level3DWindow(QMainWindow):
    TOOLBAR_HEIGHT = 52

    def __init__(self, shapes, layers, show_labels=True, perspective_enabled=True, wireframe_enabled=False):
        super().__init__()
        self.setWindowTitle("3D 关卡预览")
        if not (icon := find_resource_icon()).isNull(): self.setWindowIcon(icon)
        self.opengl_view = OpenGLLevelView(shapes, layers, show_labels)
        self.opengl_view.set_perspective_enabled(perspective_enabled); self.opengl_view.set_wireframe_enabled(wireframe_enabled); self.opengl_view.setFixedSize(OpenGLLevelView.PREVIEW_WIDTH, OpenGLLevelView.PREVIEW_HEIGHT)
        self.show_labels_checkbox, self.perspective_checkbox, self.wireframe_checkbox = QCheckBox("显示备注"), QCheckBox("透视"), QCheckBox("线框")
        self.reset_camera_button = QPushButton("摄像机回原位")
        self.reset_camera_button.clicked.connect(self.opengl_view.reset_camera)
        self.show_labels_checkbox.setChecked(show_labels); self.perspective_checkbox.setChecked(perspective_enabled); self.wireframe_checkbox.setChecked(wireframe_enabled)
        self.show_labels_checkbox.toggled.connect(self.opengl_view.set_show_labels); self.perspective_checkbox.toggled.connect(self.opengl_view.set_perspective_enabled); self.wireframe_checkbox.toggled.connect(self.opengl_view.set_wireframe_enabled)
        self.export_image_button = QPushButton("导出当前视角图片"); self.export_image_button.clicked.connect(self.on_export_current_view_image)

        toolbar = QWidget(); toolbar.setFixedHeight(self.TOOLBAR_HEIGHT)
        tl = QHBoxLayout(toolbar); tl.setContentsMargins(8, 8, 8, 8)
        for w in (self.show_labels_checkbox, self.perspective_checkbox, self.wireframe_checkbox, self.reset_camera_button, self.export_image_button): tl.addWidget(w)
        tl.addStretch()

        cw = QWidget(); cl = QVBoxLayout(); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)
        cl.addWidget(toolbar, 0); cl.addWidget(self.opengl_view, 0, Qt.AlignmentFlag.AlignCenter); cw.setLayout(cl)
        self.setCentralWidget(cw); self.setFixedSize(OpenGLLevelView.PREVIEW_WIDTH, OpenGLLevelView.PREVIEW_HEIGHT + self.TOOLBAR_HEIGHT)

    def on_export_current_view_image(self):
        fp, _ = QFileDialog.getSaveFileName(self, "导出当前摄像机画面", "3d_view.png", "PNG 图片 (*.png);;JPG 图片 (*.jpg *.jpeg)")
        if fp:
            if not fp.lower().endswith((".png", ".jpg", ".jpeg")): fp += ".png"
            if self.opengl_view.export_current_view_image(fp): QMessageBox.information(self, "导出成功", f"图片已保存到：\n{fp}")
            else: QMessageBox.warning(self, "导出失败", "截图保存失败。")


class Custom3DEditorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自定义 3D 图形编辑器")
        self.resize(800, 600)
        self.parts = []
        self.image_base64 = ""
        
        main_layout = QHBoxLayout(self)
        left_layout = QVBoxLayout()
        
        btn_upload_img = QPushButton("上传图标 (必选)")
        btn_upload_img.clicked.connect(self.upload_image)
        self.img_label = QLabel("未上传图标")
        
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self.on_part_selected)
        
        btns = QHBoxLayout()
        btn_add_box = QPushButton("加长方体")
        btn_add_cylinder = QPushButton("加圆柱体")
        btn_add_sphere = QPushButton("加球体")
        btn_remove = QPushButton("删除选中")
        btn_add_box.clicked.connect(lambda: self.add_part("box"))
        btn_add_cylinder.clicked.connect(lambda: self.add_part("cylinder"))
        btn_add_sphere.clicked.connect(lambda: self.add_part("sphere"))
        btn_remove.clicked.connect(self.remove_part)
        btns.addWidget(btn_add_box); btns.addWidget(btn_add_cylinder); btns.addWidget(btn_add_sphere); btns.addWidget(btn_remove)
        
        btns2 = QHBoxLayout()
        btn_add_cone = QPushButton("加圆锥体")
        btn_add_pyramid = QPushButton("加立方锥")
        btn_add_cone.clicked.connect(lambda: self.add_part("cone"))
        btn_add_pyramid.clicked.connect(lambda: self.add_part("pyramid"))
        btns2.addWidget(btn_add_cone); btns2.addWidget(btn_add_pyramid); btns2.addStretch()
        
        self.prop_widgets = {}
        prop_layout = QFormLayout()
        for key in ["x", "y", "z", "w", "h", "d"]:
            sp = QDoubleSpinBox(); sp.setRange(-100, 100); sp.setSingleStep(0.1)
            sp.valueChanged.connect(self.on_prop_changed)
            self.prop_widgets[key] = sp
            prop_layout.addRow(key.upper(), sp)
        
        self.btn_color = QPushButton("选择颜色")
        self.btn_color.clicked.connect(self.pick_color)
        prop_layout.addRow("颜色", self.btn_color)
        
        left_layout.addWidget(btn_upload_img)
        left_layout.addWidget(self.img_label)
        left_layout.addWidget(QLabel("部件列表:"))
        left_layout.addWidget(self.list_widget)
        left_layout.addLayout(btns)
        left_layout.addLayout(btns2)
        left_layout.addWidget(QLabel("属性:"))
        left_layout.addLayout(prop_layout)
        
        btns_bottom = QHBoxLayout()
        btn_ok = QPushButton("保存并退出"); btn_cancel = QPushButton("取消")
        btn_ok.clicked.connect(self.on_accept); btn_cancel.clicked.connect(self.reject)
        btns_bottom.addStretch(); btns_bottom.addWidget(btn_ok); btns_bottom.addWidget(btn_cancel)
        left_layout.addLayout(btns_bottom)
        
        self.preview_shape = ShapeData(shape_type="custom_3d", x=0, y=0, base_z=0, layer_index=0)
        self.preview_view = OpenGLLevelView([self.preview_shape], [LayerData("1", 0.0)], False)
        self.preview_view.set_perspective_enabled(True)
        
        main_layout.addLayout(left_layout, 1)
        main_layout.addWidget(self.preview_view, 2)
        
    def add_part(self, ptype):
        self.parts.append({"type": ptype, "x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0, "h": 1.0, "d": 1.0, "color": (200, 200, 200)})
        self.list_widget.addItem(f"{ptype} {len(self.parts)}")
        self.list_widget.setCurrentRow(len(self.parts)-1)
        self.update_preview()
        
    def remove_part(self):
        if 0 <= (idx := self.list_widget.currentRow()) < len(self.parts):
            self.parts.pop(idx); self.list_widget.takeItem(idx); self.update_preview()
            
    def on_part_selected(self, idx):
        if 0 <= idx < len(self.parts):
            part = self.parts[idx]
            for w in self.prop_widgets.values(): w.blockSignals(True)
            for k in ["x", "y", "z", "w", "h", "d"]: self.prop_widgets[k].setValue(part[k])
            for w in self.prop_widgets.values(): w.blockSignals(False)
            
    def on_prop_changed(self):
        if 0 <= (idx := self.list_widget.currentRow()) < len(self.parts):
            for k in ["x", "y", "z", "w", "h", "d"]: self.parts[idx][k] = self.prop_widgets[k].value()
            self.update_preview()
            
    def pick_color(self):
        if 0 <= (idx := self.list_widget.currentRow()) < len(self.parts):
            if (c := QColorDialog.getColor()).isValid():
                self.parts[idx]["color"] = c.toTuple()[:3]; self.update_preview()
                
    def upload_image(self):
        if fp := QFileDialog.getOpenFileName(self, "选择图标", "", "图片 (*.png *.jpg *.jpeg)")[0]:
            with open(fp, "rb") as f: self.image_base64 = base64.b64encode(f.read()).decode('utf-8')
            self.img_label.setText(f"已选: {os.path.basename(fp)}")
            
    def update_preview(self):
        self.preview_shape.custom_3d_parts = self.parts; self.preview_view.update()
        
    def on_accept(self):
        if not self.image_base64: return QMessageBox.warning(self, "错误", "必须上传一个图标")
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.base_window_title = "关卡地图设计工具"
        self.setWindowTitle(self.base_window_title); self.resize(1500, 920)
        if not (icon := find_resource_icon()).isNull(): self.setWindowIcon(icon)
        self.layers = [LayerData("第1层", 0.0), LayerData("第2层", 3.0), LayerData("第3层", 6.0)]
        self.current_item = self.preview_window = None
        self.is_3d_label_visible, self.is_3d_perspective_enabled, self.is_3d_wireframe_enabled = True, True, False
        self.current_design_file, self.default_line_color, self.default_line_width, self.is_dirty = False, QColor(80, 80, 80), 0.12, False
        self.scene = DesignScene(); self.scene.main_window = self
        self.scene.item_selected.connect(self.on_item_selected); self.scene.item_deleted.connect(self.on_item_deleted); self.scene.item_changed.connect(self.mark_dirty)
        self.view = DesignView(self.scene, self)
        self.history_snapshots, self.history_index, self.is_history_suppressed = [], -1, False
        self.clipboard_shapes, self.paste_offset_index, self.shortcut_settings = [], 0, dict(DEFAULT_SHORTCUTS)

        self.tool_list = ToolListWidget(); self.tool_list.setFixedWidth(180)
        for t, n in [("select", "🖱 选择"), ("square", "■ 正方形"), ("circle", "● 圆形"), ("ellipse", "⬭ 椭圆形"), ("triangle", "▲ 三角形"), ("rectangle", "▭ 长方形"), ("sphere", "🔴 球体"), ("cone", "🔽 圆锥体"), ("pyramid", "◮ 立方锥体"), ("line", "╱ 线条"), ("text", "T 文字"), ("stair", "🪜 楼梯"), ("player", "🧍 玩家"), ("enemy", "👾 敌人")]:
            item = QListWidgetItem(n); item.setData(Qt.ItemDataRole.UserRole, t); self.tool_list.addItem(item)
        self.tool_list.setCurrentRow(0); self.layer_list = QListWidget(); self.layer_list.setFixedWidth(220)
        self.refresh_layer_list(); self.layer_list.setCurrentRow(0)

        self.add_layer_button = self._create_btn("新增图层", self.on_add_layer); self.edit_layer_button = self._create_btn("编辑图层", self.on_edit_layer); self.delete_layer_button = self._create_btn("删除图层", self.on_delete_layer)
        self.undo_button = self._create_btn("撤销", self.undo); self.redo_button = self._create_btn("重做", self.redo); self.shortcut_settings_button = self._create_btn("快捷键设置", self.on_open_shortcut_settings)
        self.depth_spin = self._create_spinbox(0.05, 9999.0, 0.05, " m", 1.0, self.on_depth_changed); self.base_z_spin = self._create_spinbox(-9999.0, 9999.0, 0.1, " m", 0.0, self.on_base_z_changed)
        self.font_size_spin = self._create_spinbox(1.0, 200.0, 1.0, " pt", 1.0, self.on_font_size_changed)
        self.label_edit, self.text_content_edit = QLineEdit(), QLineEdit(); self.size_label = QLabel("W: -, H: -, D: -, Z: -, 真实Z: -")
        self.label_edit.textChanged.connect(self.on_label_changed); self.text_content_edit.textChanged.connect(self.on_text_content_changed); self.color_button = self._create_btn("选择颜色", self.on_pick_color)
        self.shape_layer_combo, self.stair_start_combo, self.stair_end_combo, self.stair_direction_combo = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        self.stair_auto_height_cb = QCheckBox("自动计算高度"); self.stair_auto_height_cb.setChecked(True); self.stair_auto_height_cb.toggled.connect(self.on_stair_auto_height_changed)
        self.shape_layer_combo.currentIndexChanged.connect(self.on_shape_layer_changed); self.stair_start_combo.currentIndexChanged.connect(self.on_stair_binding_changed); self.stair_end_combo.currentIndexChanged.connect(self.on_stair_binding_changed)
        self.stair_direction_combo.addItems(["正向", "反向"]); self.stair_direction_combo.setItemData(0, "forward"); self.stair_direction_combo.setItemData(1, "backward"); self.stair_direction_combo.currentIndexChanged.connect(self.on_stair_direction_changed)
        self.new_design_button = self._create_btn("新建设计栏", self.on_new_design); self.open_design_button = self._create_btn("打开设计稿", self.on_open_design)
        self.save_design_button = self._create_btn("保存设计稿", lambda: self._perform_save(False)); self.save_as_design_button = self._create_btn("设计稿另存为", lambda: self._perform_save(True))
        self.export_design_button = self._create_btn("导出设计栏图片", self.on_export_design_image); self.generate_button = self._create_btn("生成 3D 视图", self.on_generate_3d)
        self.watermark_label = QLabel("伍冠宇出品 必属精品"); self.watermark_label.setAlignment(Qt.AlignmentFlag.AlignCenter); self.watermark_label.setStyleSheet("color: #9aa3ad; font-size: 12px; padding: 6px 0;")

        self.btn_add_2d = self._create_btn("新增 2D 图标", self.on_add_custom_2d)
        self.btn_add_3d = self._create_btn("新增 3D 图形", self.on_add_custom_3d)

        self.delete_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self); self.delete_shortcut.activated.connect(self.delete_selected_items)
        for a, f in [("undo", self.undo), ("redo", self.redo), ("copy", self.copy_selected_items), ("cut", self.cut_selected_items), ("paste", self.paste_items)]:
            sc = QShortcut(QKeySequence(), self); sc.activated.connect(f); setattr(self, f"{a}_shortcut", sc)

        self.init_ui(); self.apply_shortcuts(); self.clear_properties(); self.mark_clean(); self.push_history_snapshot("init", force=True)

    def _create_btn(self, text, slot) -> QPushButton:
        b = QPushButton(text); b.clicked.connect(slot); return b

    def _create_spinbox(self, rmin, rmax, step, suf, val, slot) -> QDoubleSpinBox:
        sb = QDoubleSpinBox(); sb.setRange(rmin, rmax); sb.setSingleStep(step); sb.setSuffix(suf); sb.setValue(val); sb.valueChanged.connect(slot); return sb

    def get_layer_z_offset(self, idx: int) -> float: return self.layers[idx].z_offset if 0 <= idx < len(self.layers) else 0.0

    def get_real_base_z(self, shape: ShapeData) -> float:
        return get_real_base_z_with_adjustment(shape, self.scene.get_all_shapes(), self.layers)

    def init_ui(self):
        cw = QWidget(); self.setCentralWidget(cw); ml = QHBoxLayout(cw)
        lp, ll = QWidget(), QVBoxLayout(); lp.setLayout(ll)
        ll.addWidget(QLabel("工具栏（拖拽到设计区创建图形，线条为点击拖拽绘制）")); ll.addWidget(self.tool_list)
        ll.addWidget(self.btn_add_2d); ll.addWidget(self.btn_add_3d)
        ll.addWidget(QLabel("图层")); ll.addWidget(self.layer_list)
        for w in (self.add_layer_button, self.edit_layer_button, self.delete_layer_button): ll.addWidget(w)
        pw, pl = QWidget(), QFormLayout(); pw.setLayout(pl)
        depth_widget = QWidget(); dl = QHBoxLayout(depth_widget); dl.setContentsMargins(0, 0, 0, 0); dl.addWidget(self.depth_spin); dl.addWidget(self.stair_auto_height_cb)
        for w, t in [(self.label_edit, "标签"), (self.text_content_edit, "文字内容"), (self.font_size_spin, "字号"), (self.shape_layer_combo, "所属图层"), (self.base_z_spin, "底部高度"), (depth_widget, "高度/线宽"), (self.stair_start_combo, "楼梯起点平台"), (self.stair_end_combo, "楼梯终点平台"), (self.stair_direction_combo, "楼梯方向"), (self.size_label, "尺寸"), (self.color_button, "颜色")]: pl.addRow(t, w)
        ll.addWidget(QLabel("属性")); ll.addWidget(pw); ll.addStretch()
        rp, rl = QWidget(), QVBoxLayout(); rp.setLayout(rl)
        rl.addWidget(QLabel("设计栏（顶视图，单位：米）")); rl.addWidget(self.view)
        fl, al = QHBoxLayout(), QHBoxLayout()
        for w in (self.new_design_button, self.open_design_button, self.save_design_button, self.save_as_design_button, self.undo_button, self.redo_button, self.shortcut_settings_button): fl.addWidget(w)
        for w in (self.export_design_button, self.generate_button): al.addWidget(w)
        rl.addLayout(fl); rl.addLayout(al); rl.addWidget(self.watermark_label)
        sp = QSplitter(); sp.addWidget(lp); sp.addWidget(rp); sp.setStretchFactor(1, 1); ml.addWidget(sp)
        self.refresh_shape_layer_combo(); self.refresh_stair_binding_combos()

    def on_add_custom_2d(self):
        fp, _ = QFileDialog.getOpenFileName(self, "选择 2D 图标", "", "图片 (*.png *.jpg *.jpeg)")
        if fp:
            default_name = f"🖼 {os.path.basename(fp)}"
            name, ok = QInputDialog.getText(self, "自定义名称", "请输入 2D 图标名称:", text=default_name)
            if ok and name.strip():
                with open(fp, "rb") as f: b64 = base64.b64encode(f.read()).decode('utf-8')
                item = QListWidgetItem(name.strip())
                item.setData(Qt.ItemDataRole.UserRole, "custom_2d")
                item.setData(Qt.ItemDataRole.UserRole + 1, b64)
                self.tool_list.addItem(item)

    def on_add_custom_3d(self):
        dlg = Custom3DEditorDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, ok = QInputDialog.getText(self, "自定义名称", "请输入 3D 图形名称:", text="🧊 自定义 3D")
            if ok and name.strip():
                item = QListWidgetItem(name.strip())
                item.setData(Qt.ItemDataRole.UserRole, "custom_3d")
                item.setData(Qt.ItemDataRole.UserRole + 1, dlg.image_base64)
                item.setData(Qt.ItemDataRole.UserRole + 2, dlg.parts)
                self.tool_list.addItem(item)

    def apply_shortcuts(self) -> None:
        for a in ["undo", "redo", "copy", "cut", "paste"]: getattr(self, f"{a}_shortcut").setKey(QKeySequence(self.shortcut_settings[a]))
        self.undo_button.setToolTip(f"快捷键：{self.shortcut_settings['undo']}"); self.redo_button.setToolTip(f"快捷键：{self.shortcut_settings['redo']}"); self.shortcut_settings_button.setToolTip("可设置撤销/重做/复制/剪切/粘贴快捷键")

    def on_open_shortcut_settings(self) -> None:
        dlg = ShortcutSettingsDialog(self, self.shortcut_settings)
        if dlg.exec() == QDialog.DialogCode.Accepted: self.shortcut_settings = dlg.get_shortcuts(); self.apply_shortcuts(); self.mark_dirty()

    def update_window_title(self): self.setWindowTitle(f"{self.base_window_title} - {os.path.basename(self.current_design_file) if self.current_design_file else '未命名'}{' *' if self.is_dirty else ''}")
    def mark_dirty(self):
        if not self.is_dirty: self.is_dirty = True; self.update_window_title()
    def mark_clean(self): self.is_dirty = False; self.update_window_title()

    def build_snapshot(self) -> dict:
        return {"layers": [asdict(l) for l in self.layers], "shapes": [asdict(s) for s in self.scene.get_all_shapes()], "view": {"show_labels": self.is_3d_label_visible, "perspective_enabled": self.is_3d_perspective_enabled, "wireframe_enabled": self.is_3d_wireframe_enabled}, "shortcuts": dict(self.shortcut_settings), "current_design_file": self.current_design_file}

    def restore_snapshot(self, sn: dict) -> None:
        self.is_history_suppressed = True
        try:
            self.layers = [LayerData(l.get("name", "未命名"), float(l.get("z_offset", 0.0))) for l in sn.get("layers", [])] or [LayerData("第1层", 0.0)]
            valid_keys = inspect.signature(ShapeData).parameters.keys()
            shapes = []
            for s in sn.get("shapes", []):
                kwargs = {k: (tuple(v) if k == "color" and isinstance(v, list) else v) for k, v in s.items() if k in valid_keys}
                shapes.append(ShapeData(**kwargs))
            vd = sn.get("view", {})
            self.is_3d_label_visible, self.is_3d_perspective_enabled, self.is_3d_wireframe_enabled = bool(vd.get("show_labels", True)), bool(vd.get("perspective_enabled", True)), bool(vd.get("wireframe_enabled", False))
            for k, dv in DEFAULT_SHORTCUTS.items(): self.shortcut_settings[k] = sn.get("shortcuts", {}).get(k, dv)
            self.scene.load_from_data(self.layers, shapes); self.refresh_layer_list(); self.refresh_shape_layer_combo(); self.refresh_stair_binding_combos(); self.layer_list.setCurrentRow(0); self.clear_properties(); self.apply_shortcuts(); self.current_design_file = sn.get("current_design_file", self.current_design_file)
        finally: self.is_history_suppressed = False

    def push_history_snapshot(self, reason: str = "", force: bool = False) -> None:
        if self.is_history_suppressed and not force: return
        sn = self.build_snapshot()
        if not force and 0 <= self.history_index < len(self.history_snapshots) and self.history_snapshots[self.history_index] == sn: return
        self.history_snapshots = self.history_snapshots[:self.history_index + 1] + [sn]
        if len(self.history_snapshots) > 100: self.history_snapshots = self.history_snapshots[-100:]
        self.history_index = len(self.history_snapshots) - 1

    def undo(self) -> None:
        if self.history_index > 0: self.history_index -= 1; self.restore_snapshot(self.history_snapshots[self.history_index]); self.mark_dirty()
    def redo(self) -> None:
        if self.history_index < len(self.history_snapshots) - 1: self.history_index += 1; self.restore_snapshot(self.history_snapshots[self.history_index]); self.mark_dirty()

    def get_current_tool_type(self) -> Optional[str]: return self.tool_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.tool_list.currentItem() else None
    def get_default_line_color(self): return self.default_line_color.red(), self.default_line_color.green(), self.default_line_color.blue()
    def get_default_line_width(self): return self.default_line_width

    def refresh_layer_list(self):
        self.layer_list.clear()
        for i, l in enumerate(self.layers):
            it = QListWidgetItem(f"{l.name} (Z={l.z_offset:.2f}m)"); it.setData(Qt.ItemDataRole.UserRole, i); self.layer_list.addItem(it)

    def refresh_shape_layer_combo(self):
        self.shape_layer_combo.blockSignals(True); self.shape_layer_combo.clear()
        for i, l in enumerate(self.layers): self.shape_layer_combo.addItem(f"{l.name} (Z={l.z_offset:.2f}m)", i)
        self.shape_layer_combo.blockSignals(False)

    def refresh_stair_binding_combos(self):
        cands = [s for s in self.scene.get_all_shapes() if s.shape_type in PLATFORM_TYPES]
        for cb in (self.stair_start_combo, self.stair_end_combo): cb.blockSignals(True); cb.clear(); cb.addItem("自动识别", "")
        for i, c in enumerate(cands):
            rz = self.get_real_base_z(c)
            prefix = f"[{c.label}] " if c.label else ""
            n = f"{prefix}{i + 1}. {c.shape_type} (x={c.x:.1f}, y={c.y:.1f}, w={c.width:.1f}, h={c.height:.1f}, z={rz:.1f})"
            k = f"{c.shape_type}|{c.layer_index}|{c.x:.3f}|{c.y:.3f}|{c.width:.3f}|{c.height:.3f}|{rz:.3f}"
            self.stair_start_combo.addItem(n, k); self.stair_end_combo.addItem(n, k)
        for cb in (self.stair_start_combo, self.stair_end_combo): cb.blockSignals(False)

    def get_current_layer_index(self): return max(0, self.layer_list.currentRow())
    def get_current_layer_z(self): return self.layers[idx].z_offset if 0 <= (idx := self.get_current_layer_index()) < len(self.layers) else 0.0

    def on_add_layer(self):
        dlg = LayerDialog(self, f"第{len(self.layers) + 1}层", 0.0)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.layers.append(LayerData(*dlg.get_data())); self.refresh_layer_list(); self.refresh_shape_layer_combo()
            self.layer_list.setCurrentRow(len(self.layers) - 1); self.mark_dirty(); self.push_history_snapshot("add_layer")

    def on_edit_layer(self):
        idx = self.get_current_layer_index()
        if 0 <= idx < len(self.layers):
            dlg = LayerDialog(self, self.layers[idx].name, self.layers[idx].z_offset)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.layers[idx] = LayerData(*dlg.get_data()); self.refresh_layer_list(); self.refresh_shape_layer_combo(); self.refresh_stair_binding_combos(); self.layer_list.setCurrentRow(idx)
                if self.current_item and self.current_item.scene(): self.on_item_selected(self.current_item)
                self.mark_dirty(); self.push_history_snapshot("edit_layer")

    def on_delete_layer(self):
        if len(self.layers) <= 1: return QMessageBox.information(self, "提示", "至少需要保留一个图层。")
        idx = self.get_current_layer_index()
        if 0 <= idx < len(self.layers) and QMessageBox.question(self, "确认", "确定删除该图层？") == QMessageBox.StandardButton.Yes:
            del self.layers[idx]
            for it in [it for it in self.scene.items() if isinstance(it, BaseDesignItem)]:
                if it.shape_data.layer_index >= idx: it.shape_data.layer_index = max(0, it.shape_data.layer_index - 1)
            self.refresh_layer_list(); self.refresh_shape_layer_combo(); self.refresh_stair_binding_combos(); self.layer_list.setCurrentRow(0)
            if self.current_item and self.current_item.scene(): self.on_item_selected(self.current_item)
            else: self.clear_properties()
            self.mark_dirty(); self.push_history_snapshot("delete_layer")

    def _toggle_property_signals(self, block: bool):
        for w in (self.label_edit, self.text_content_edit, self.font_size_spin, self.shape_layer_combo, self.base_z_spin, self.depth_spin, self.stair_start_combo, self.stair_end_combo, self.stair_direction_combo, self.stair_auto_height_cb): w.blockSignals(block)

    def on_item_selected(self, item):
        if not item or not item.scene(): return self.clear_properties()
        self.current_item = item; self.refresh_stair_binding_combos(); self._toggle_property_signals(True)
        sd, rz = item.shape_data, self.get_real_base_z(item.shape_data)
        self.label_edit.setText(sd.label); self.font_size_spin.setValue(sd.font_size); self.base_z_spin.setValue(sd.base_z)
        ist, isl, iss = isinstance(item, TextItem), isinstance(item, LineItem), isinstance(item, ShapeItem) and sd.shape_type == "stair"
        self.text_content_edit.setEnabled(ist); self.stair_start_combo.setEnabled(iss); self.stair_end_combo.setEnabled(iss); self.stair_direction_combo.setEnabled(iss)
        self.stair_auto_height_cb.setVisible(iss)
        if ist:
            self.text_content_edit.setText(sd.text_content); self.depth_spin.setEnabled(False); self.depth_spin.setValue(0.05)
            self.size_label.setText(f"T: {sd.text_content or '-'}, 相对Z: {sd.base_z:.2f} m, 真实Z: {rz:.2f} m")
        elif isl:
            self.text_content_edit.clear(); self.depth_spin.setEnabled(True); self.depth_spin.setValue(sd.line_width)
            self.size_label.setText(f"{'曲线' if sd.line_mode == 'curve' else '直线'} L: {item.shape_length():.2f} m, T: {sd.line_width:.2f} m, 相对Z: {sd.base_z:.2f} m, 真实Z: {rz:.2f} m")
        else:
            self.text_content_edit.clear(); self.depth_spin.setValue(sd.depth)
            if iss:
                self.stair_auto_height_cb.setChecked(getattr(sd, 'stair_auto_height', True))
                self.depth_spin.setEnabled(not getattr(sd, 'stair_auto_height', True))
            else:
                self.depth_spin.setEnabled(not item.is_character_shape())
            self.size_label.setText(f"W: {sd.width:.2f} m, H: {sd.height:.2f} m, D: {sd.depth:.2f} m, 相对Z: {sd.base_z:.2f} m, 真实Z: {rz:.2f} m")
        if 0 <= sd.layer_index < self.shape_layer_combo.count(): self.shape_layer_combo.setCurrentIndex(sd.layer_index)
        for cb, v in [(self.stair_start_combo, sd.stair_start_binding if iss else ""), (self.stair_end_combo, sd.stair_end_binding if iss else ""), (self.stair_direction_combo, sd.stair_direction if iss else "forward")]:
            cb.setCurrentIndex(max(0, cb.findData(v)))
        self._toggle_property_signals(False)

    def on_item_deleted(self, item):
        if self.current_item is item: self.clear_properties(); self.refresh_stair_binding_combos()
        self.mark_dirty(); self.push_history_snapshot("delete_item")

    def clear_properties(self):
        self.current_item = None; self._toggle_property_signals(True)
        self.label_edit.clear(); self.text_content_edit.clear(); self.text_content_edit.setEnabled(False); self.font_size_spin.setValue(1.0); self.base_z_spin.setValue(0.0); self.depth_spin.setValue(1.0); self.depth_spin.setEnabled(True); self.stair_start_combo.setEnabled(False); self.stair_end_combo.setEnabled(False); self.stair_direction_combo.setEnabled(False)
        self.stair_auto_height_cb.setVisible(False)
        if self.shape_layer_combo.count() > 0: self.shape_layer_combo.setCurrentIndex(0)
        for cb, v in [(self.stair_start_combo, ""), (self.stair_end_combo, ""), (self.stair_direction_combo, "forward")]: cb.setCurrentIndex(max(0, cb.findData(v)))
        self.size_label.setText("W: -, H: -, D: -, 相对Z: -, 真实Z: -"); self._toggle_property_signals(False)

    def _apply_item_change(self, reason: str, update_ui: bool = True):
        if update_ui and self.current_item: self.on_item_selected(self.current_item)
        self.mark_dirty(); self.push_history_snapshot(reason)

    def on_depth_changed(self, value):
        if not self.current_item or isinstance(self.current_item, TextItem): return
        if isinstance(self.current_item, LineItem): self.current_item.set_line_width(value); self.default_line_width = value
        else: self.current_item.set_depth(value)
        self._apply_item_change("depth_changed")

    def on_base_z_changed(self, value):
        if self.current_item: self.current_item.set_base_z(value); self._apply_item_change("base_z_changed")

    def on_label_changed(self, text):
        if self.current_item: self.current_item.set_label(text); self._apply_item_change("label_changed", update_ui=False)

    def on_text_content_changed(self, text):
        if isinstance(self.current_item, TextItem): self.current_item.set_text_content(text); self.current_item.shape_data.text_width = max(3.0, len(text or "文字") * 0.45); self._apply_item_change("text_changed")

    def on_font_size_changed(self, value):
        if self.current_item: self.current_item.set_font_size(value); self._apply_item_change("font_size_changed")

    def on_shape_layer_changed(self, idx):
        if self.current_item and 0 <= idx < len(self.layers): self.current_item.shape_data.layer_index = idx; self._apply_item_change("shape_layer_changed")

    def on_stair_binding_changed(self, _):
        if isinstance(self.current_item, ShapeItem) and self.current_item.shape_data.shape_type == "stair":
            self.current_item.shape_data.stair_start_binding, self.current_item.shape_data.stair_end_binding = self.stair_start_combo.currentData() or "", self.stair_end_combo.currentData() or ""
            self.current_item.update(); self._apply_item_change("stair_binding_changed", update_ui=False)

    def on_stair_direction_changed(self, _):
        if isinstance(self.current_item, ShapeItem) and self.current_item.shape_data.shape_type == "stair":
            self.current_item.shape_data.stair_direction = self.stair_direction_combo.currentData() or "forward"
            self.current_item.update(); self._apply_item_change("stair_direction_changed", update_ui=False)

    def on_stair_auto_height_changed(self, checked):
        if self.current_item and isinstance(self.current_item, ShapeItem) and self.current_item.shape_data.shape_type == "stair":
            self.current_item.shape_data.stair_auto_height = checked
            self.depth_spin.setEnabled(not checked)
            self._apply_item_change("stair_auto_height_changed", update_ui=False)

    def on_pick_color(self):
        c = QColorDialog.getColor(QColor(*self.current_item.shape_data.color) if self.current_item else self.default_line_color, self, "选择颜色")
        if c.isValid():
            if self.current_item: self.current_item.set_color(c)
            if not self.current_item or isinstance(self.current_item, LineItem): self.default_line_color = c
            self._apply_item_change("pick_color", update_ui=False)

    def get_selected_design_items(self) -> List[BaseDesignItem]: return [it for it in self.scene.selectedItems() if isinstance(it, BaseDesignItem)]
    def clone_shape_data(self, sd: ShapeData) -> ShapeData: return ShapeData(**{k: (tuple(v) if k == "color" and isinstance(v, list) else v) for k, v in asdict(sd).items()})

    def copy_selected_items(self):
        if sitems := self.get_selected_design_items(): self.clipboard_shapes, self.paste_offset_index = [self.clone_shape_data(it.shape_data) for it in sorted(sitems, key=lambda i: i.zValue())], 0

    def cut_selected_items(self):
        if sitems := self.get_selected_design_items():
            self.copy_selected_items()
            for it in sitems: it.delete_item()
            self.clear_properties(); self.refresh_stair_binding_combos(); self.mark_dirty(); self.push_history_snapshot("cut")

    def paste_items(self):
        if not self.clipboard_shapes: return
        self.is_history_suppressed = True
        try:
            self.scene.clearSelection(); self.paste_offset_index += 1; off, cis = 0.6 * self.paste_offset_index, []
            for src in self.clipboard_shapes:
                sd = self.clone_shape_data(src)
                sd.x += off; sd.y += off; sd.end_x += off; sd.end_y += off
                if sd.control_points: sd.control_points = [(x + off, y + off) for x, y in sd.control_points]
                it = LineItem(sd) if sd.shape_type == "line" else TextItem(sd) if sd.shape_type == "text" else ShapeItem(sd)
                self.scene._init_item(it, None if sd.shape_type == "line" else QPointF(sd.x, sd.y))
                it.setSelected(True); cis.append(it)
            self.scene.normalize_z_orders()
            if cis: self.on_item_selected(cis[-1])
            self.refresh_stair_binding_combos()
        finally: self.is_history_suppressed = False
        self.scene.item_changed.emit(); self.mark_dirty(); self.push_history_snapshot("paste")

    def show_empty_context_menu(self, pos: QPoint) -> None:
        m = QMenu()
        paste_act = m.addAction("粘贴")
        reset_act = m.addAction("回到世界原点")
        res = m.exec(pos)
        if res == paste_act: self.paste_items()
        elif res == reset_act: self.view.centerOn(0, 0)

    def delete_selected_items(self):
        if sitems := self.get_selected_design_items():
            for it in sitems: it.delete_item()
            self.clear_properties(); self.refresh_stair_binding_combos(); self.mark_dirty(); self.push_history_snapshot("delete_selected")

    def reset_to_new_design(self):
        self.is_history_suppressed = True
        try:
            self.scene.clear(); self.layers = [LayerData("第1层", 0.0), LayerData("第2层", 3.0), LayerData("第3层", 6.0)]
            self.current_item = self.preview_window = None; self.current_design_file = ""; self.clipboard_shapes = []; self.paste_offset_index = 0
            self.refresh_layer_list(); self.refresh_shape_layer_combo(); self.refresh_stair_binding_combos(); self.layer_list.setCurrentRow(0)
            self.clear_properties(); self.mark_clean()
        finally: self.is_history_suppressed = False
        self.history_snapshots, self.history_index = [], -1; self.push_history_snapshot("reset", force=True)

    def _perform_save(self, save_as=False) -> bool:
        fp = "" if save_as else self.current_design_file
        if not fp:
            fp, _ = QFileDialog.getSaveFileName(self, "设计稿另存为", "design.json", "设计稿文件 (*.json);;所有文件 (*.*)")
            if not fp: return False
            if not fp.lower().endswith(".json"): fp += ".json"
        try:
            with open(fp, "w", encoding="utf-8") as f: json.dump(self.build_snapshot(), f, ensure_ascii=False, indent=2)
            self.current_design_file = fp; self.mark_clean()
            if not save_as: QMessageBox.information(self, "保存成功", f"已保存到：\n{fp}")
            return True
        except OSError: return QMessageBox.warning(self, "保存失败", "保存失败。") or False

    def maybe_save_before_destructive_action(self, action_name: str) -> bool:
        if not self.is_dirty: return True
        res = QMessageBox.question(self, action_name, "当前有未保存变更，是否先保存？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
        if res == QMessageBox.StandardButton.Cancel: return False
        return True if res == QMessageBox.StandardButton.No else self._perform_save()

    def on_new_design(self):
        if self.maybe_save_before_destructive_action("新建设计栏"): self.reset_to_new_design()

    def on_open_design(self):
        if not self.maybe_save_before_destructive_action("打开设计稿"): return
        if fp := QFileDialog.getOpenFileName(self, "打开设计稿", "", "设计稿文件 (*.json);;所有文件 (*.*)")[0]:
            try:
                with open(fp, "r", encoding="utf-8") as f: self.restore_snapshot(json.load(f))
                self.mark_clean(); self.history_snapshots, self.history_index = [], -1; self.push_history_snapshot("load", force=True)
                QMessageBox.information(self, "打开成功", f"已打开设计稿：\n{fp}")
            except (OSError, json.JSONDecodeError, TypeError, ValueError): QMessageBox.warning(self, "打开失败", "设计稿文件读取失败。")

    def on_export_design_image(self):
        if not self.scene.get_all_shapes(): return QMessageBox.information(self, "提示", "设计栏中还没有图形。")
        fp, _ = QFileDialog.getSaveFileName(self, "导出设计栏图片", "design_view.png", "PNG 图片 (*.png);;JPG 图片 (*.jpg *.jpeg)")
        if not fp: return
        if not fp.lower().endswith((".png", ".jpg", ".jpeg")): fp += ".png"
        ir, sr = self.scene.itemsBoundingRect(), self.scene.sceneRect()
        tr = sr if ir.isNull() or ir.width() <= 0 or ir.height() <= 0 else ir.united(sr.intersected(ir)).adjusted(-2, -2, 2, 2)
        w, h = max(800, int(tr.width() * 60)), max(800, int(tr.height() * 60))
        img = QImage(w, h, QImage.Format.Format_ARGB32); img.fill(QColor(255, 255, 255))
        p = QPainter(img); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.scene.render(p, QRectF(0, 0, w, h), tr, Qt.AspectRatioMode.KeepAspectRatio); p.end()
        if img.save(fp, "PNG" if fp.lower().endswith(".png") else "JPG"): QMessageBox.information(self, "导出成功", f"图片已保存到：\n{fp}")
        else: QMessageBox.warning(self, "导出失败", "图片保存失败。")

    def on_generate_3d(self):
        shapes = self.scene.get_all_shapes()
        if not shapes: return QMessageBox.information(self, "提示", "设计栏中还没有图形。")
        self.preview_window = Level3DWindow(shapes, self.layers, self.is_3d_label_visible, self.is_3d_perspective_enabled, self.is_3d_wireframe_enabled)
        self.preview_window.show_labels_checkbox.toggled.connect(lambda c: setattr(self, "is_3d_label_visible", c))
        self.preview_window.perspective_checkbox.toggled.connect(lambda c: setattr(self, "is_3d_perspective_enabled", c))
        self.preview_window.wireframe_checkbox.toggled.connect(lambda c: setattr(self, "is_3d_wireframe_enabled", c))
        self.preview_window.show()

    def closeEvent(self, event): event.accept() if self.maybe_save_before_destructive_action("关闭窗口") else event.ignore()


if __name__ == "__main__":
    import ctypes
    if os.name == 'nt':
        try:
            myappid = 'wuguanyu.leveldesign.tool.1'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

    app = QApplication(sys.argv)
    
    base_dir = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.dirname(os.path.abspath(__file__))
    jpg_icon_path = os.path.join(base_dir, "Resources", "icon.jpg")
    ico_icon_path = os.path.join(base_dir, "Resources", "icon.ico")

    if os.path.exists(jpg_icon_path):
        app.setWindowIcon(QIcon(jpg_icon_path))
    elif not (icon := find_resource_icon()).isNull():
        app.setWindowIcon(icon)

    window = MainWindow()
    if os.path.exists(ico_icon_path):
        window.setWindowIcon(QIcon(ico_icon_path))

    window.show()
    sys.exit(app.exec())