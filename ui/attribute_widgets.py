"""Editors for class attribute definitions and per-annotation values."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jotmark.data.attribute_schema import new_attribute_node


ATTRIBUTE_TYPE_LABELS = {
    "single_select": "单选",
    "multi_select": "多选",
    "text": "文本",
    "number": "数值",
    "boolean": "是 / 否",
}


class EditableAttributeLabel(QLabel):
    """A property label that exposes schema-name editing from the right pane."""

    edit_requested = Signal(str)

    def __init__(self, node_id, text, parent=None):
        super().__init__(text, parent)
        self.node_id = node_id
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("双击修改此属性标签（影响当前类别的全部标注）")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.edit_requested.emit(self.node_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class AttributeValuePickerDialog(QDialog):
    """Category-style searchable popup used for select and multi-select values."""

    def __init__(self, title, options, value=None, multiple=False, parent=None):
        super().__init__(parent)
        self.multiple = multiple
        self.setWindowTitle(title)
        self.resize(300, 380)

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索属性值...")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("classListWidget")
        self.list_widget.setSelectionMode(
            QAbstractItemView.SingleSelection if not multiple else QAbstractItemView.NoSelection
        )
        selected = set(value if isinstance(value, list) else ([value] if value else []))
        for option in options:
            item = QListWidgetItem(option)
            item.setData(Qt.UserRole, option)
            if multiple:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if option in selected else Qt.Unchecked)
            elif option in selected:
                item.setSelected(True)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        self.clear_button = buttons.addButton("清除", QDialogButtonBox.ResetRole)
        self.clear_button.clicked.connect(self._clear)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.search.textChanged.connect(self._filter)
        if not multiple:
            self.list_widget.itemDoubleClicked.connect(lambda _item: self.accept())

    def _filter(self, text):
        keyword = text.strip().lower()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            item.setHidden(bool(keyword) and keyword not in item.text().lower())

    def _clear(self):
        self.list_widget.clearSelection()
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if self.multiple:
                item.setCheckState(Qt.Unchecked)
        self.accept()

    def value(self):
        if self.multiple:
            return [
                self.list_widget.item(index).data(Qt.UserRole)
                for index in range(self.list_widget.count())
                if self.list_widget.item(index).checkState() == Qt.Checked
            ]
        current = self.list_widget.currentItem()
        return current.data(Qt.UserRole) if current else ""


class AttributeValuePickerButton(QPushButton):
    """Compact current-value button which opens the full category-style picker."""

    value_changed = Signal(object)

    def __init__(self, title, options, value=None, multiple=False, parent=None):
        super().__init__(parent)
        self.title = title
        self.options = list(options)
        self.multiple = multiple
        self.current_value = value if multiple else (value or "")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击选择或更换属性值")
        self.setStyleSheet("""
            QPushButton {
                min-height: 28px;
                padding: 4px 8px;
                border: 1px solid #94A3B8;
                border-radius: 5px;
                background: transparent;
                text-align: left;
            }
            QPushButton:hover { border-color: #3B82F6; background: rgba(59, 130, 246, 0.10); }
        """)
        self._refresh_text()
        self.clicked.connect(self._open_picker)

    def _refresh_text(self):
        if self.multiple:
            values = self.current_value if isinstance(self.current_value, list) else []
            self.setText("、".join(values) if values else "未选择（可多选）")
        else:
            self.setText(self.current_value or "未选择")

    def _open_picker(self):
        dialog = AttributeValuePickerDialog(self.title, self.options, self.current_value, self.multiple, self)
        if dialog.exec() == QDialog.Accepted:
            self.current_value = dialog.value()
            self._refresh_text()
            self.value_changed.emit(self.current_value)


class BooleanValuePicker(QWidget):
    """Explicit three-state boolean editor: unset, yes, or no.

    A lone checkbox makes an unchecked value ambiguous: it can mean either
    "no" or "not filled".  Two exclusive buttons keep false as an actual
    saved value while leaving both buttons unselected for a new annotation.
    """

    value_changed = Signal(object)

    def __init__(self, value=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.yes_button = QPushButton("是")
        self.no_button = QPushButton("否")
        self.clear_button = QPushButton("清除")
        for button in (self.yes_button, self.no_button):
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(28)
            button.setStyleSheet("""
                QPushButton {
                    min-width: 54px; padding: 4px 10px; border: 1px solid #94A3B8;
                    border-radius: 5px; background: transparent;
                }
                QPushButton:hover { border-color: #3B82F6; background: rgba(59, 130, 246, 0.10); }
                QPushButton:checked { color: white; border-color: #2563EB; background: #2563EB; font-weight: 600; }
            """)
        self.clear_button.setCursor(Qt.PointingHandCursor)
        self.clear_button.setMinimumHeight(28)
        self.clear_button.setToolTip("恢复为未选择")
        self.clear_button.setStyleSheet("QPushButton { border: none; color: #64748B; padding: 4px 6px; } QPushButton:hover { color: #2563EB; }")

        self.group.addButton(self.yes_button)
        self.group.addButton(self.no_button)
        layout.addWidget(self.yes_button)
        layout.addWidget(self.no_button)
        layout.addWidget(self.clear_button)
        layout.addStretch(1)

        if value is True:
            self.yes_button.setChecked(True)
        elif value is False:
            self.no_button.setChecked(True)

        self.yes_button.clicked.connect(lambda: self.value_changed.emit(True))
        self.no_button.clicked.connect(lambda: self.value_changed.emit(False))
        self.clear_button.clicked.connect(self._clear)

    def _clear(self):
        # An exclusive QButtonGroup will not normally allow clearing the last
        # checked button. Temporarily relax exclusivity for this explicit user
        # action, then restore it for the two value buttons.
        self.group.setExclusive(False)
        self.yes_button.setChecked(False)
        self.no_button.setChecked(False)
        self.group.setExclusive(True)
        self.value_changed.emit("")


class AttributeScopeDialog(QDialog):
    """Popup list of all public or class-scoped properties for one annotation."""

    def __init__(self, editor, scope, title, parent=None):
        super().__init__(parent or editor)
        self.editor = editor
        self.scope = scope
        self.setWindowTitle(title)
        self.resize(380, 520)
        self._refresh_scheduled = False
        self._visibility_signature = None

        layout = QVBoxLayout(self)
        hint = QLabel("点击属性当前值即可打开完整选项列表；双击属性名称可修改标签。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748B; font-size: 11px;")
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.content = QWidget()
        self.form = QFormLayout(self.content)
        self.form.setContentsMargins(4, 4, 4, 4)
        self.form.setSpacing(8)
        scroll.setWidget(self.content)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.editor.attributes_changed.connect(self._schedule_refresh)
        self._rebuild()

    def _clear_form(self):
        while self.form.rowCount():
            self.form.removeRow(0)

    def _rebuild(self):
        self._refresh_scheduled = False
        self._clear_form()
        nodes = self.editor._schemas.get(self.scope, [])
        if not nodes:
            self.form.addRow(QLabel("当前没有可用属性。"))
            self._visibility_signature = ()
            return
        self._add_nodes(nodes)
        self._visibility_signature = self._current_visibility_signature()

    def _current_visibility_signature(self):
        """Return the currently visible schema-node sequence.

        A value change only needs to rebuild this dialog if it adds or removes
        child rows.  Rebuilding after every single-select change deletes the
        active picker button and can make a second click unreliable on some
        Qt platform backends.
        """
        values = self.editor._shape.attributes if self.editor._shape else {}
        visible_ids = []

        def collect(nodes):
            for node in nodes:
                node_id = node.get("id")
                if not node_id:
                    continue
                visible_ids.append(node_id)
                if self.editor._has_value(values.get(node_id)):
                    collect(node.get("children", []))

        collect(self.editor._schemas.get(self.scope, []))
        return tuple(visible_ids)

    def _add_nodes(self, nodes, depth=0):
        values = self.editor._shape.attributes if self.editor._shape else {}
        for node in nodes:
            node_id = node.get("id")
            if not node_id:
                continue
            value = values.get(node_id)
            label = EditableAttributeLabel(node_id, ("　" * depth) + node.get("name", "属性"))
            label.edit_requested.connect(self.editor._request_label_edit)
            widget = self.editor._make_value_widget(node, value, self.scope)
            self.form.addRow(label, widget)
            if self.editor._has_value(value):
                self._add_nodes(node.get("children", []), depth + 1)

    def _schedule_refresh(self, _shape):
        if self._refresh_scheduled or not self.isVisible():
            return
        # Scalar edits (e.g. red -> blue) are already painted by their own
        # picker. Keep that widget alive so it can be clicked repeatedly.
        if self._visibility_signature == self._current_visibility_signature():
            return
        self._refresh_scheduled = True
        QTimer.singleShot(0, self._rebuild)

    def closeEvent(self, event):
        try:
            self.editor.attributes_changed.disconnect(self._schedule_refresh)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)


class AttributeSchemaDialog(QDialog):
    """Edit one class's independent, nested attribute definition tree."""

    def __init__(self, class_name, attributes, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"管理“{class_name}”的属性")
        self.resize(720, 480)
        self._attributes = deepcopy(attributes)
        self._updating_editor = False
        self._build_ui(class_name)
        self._refresh_tree()

    def _build_ui(self, class_name):
        layout = QVBoxLayout(self)
        if class_name == "公共属性（全部类别）":
            title_text = "公共属性会显示在全部类别的标注属性中。"
        else:
            title_text = f"类别：{class_name}　属性定义仅影响“{class_name}”标注"
        title = QLabel(title_text)
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        self.btn_add = QPushButton("+ 属性")
        self.btn_add_child = QPushButton("+ 子属性")
        self.btn_delete = QPushButton("删除")
        self.btn_up = QPushButton("上移")
        self.btn_down = QPushButton("下移")
        for button in (self.btn_add, self.btn_add_child, self.btn_delete, self.btn_up, self.btn_down):
            tools.addWidget(button)
        left_layout.addLayout(tools)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["属性", "类型"])
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setIndentation(18)
        left_layout.addWidget(self.tree, 1)
        splitter.addWidget(left)

        right = QWidget()
        form = QFormLayout(right)
        self.name_edit = QLineEdit()
        self.type_combo = QComboBox()
        for value, label in ATTRIBUTE_TYPE_LABELS.items():
            self.type_combo.addItem(label, value)
        self.options_edit = QPlainTextEdit()
        self.options_edit.setPlaceholderText("每行一个可选值\n例如：\n红\n蓝\n黑")
        self.options_edit.setFixedHeight(150)
        self.options_hint = QLabel("单选 / 多选属性的可选值，每行一个。")
        self.options_hint.setWordWrap(True)
        self.children_hint = QLabel("子属性在父属性有值时显示；不同属性互不影响。")
        self.children_hint.setWordWrap(True)
        self.children_hint.setStyleSheet("color: #64748B;")
        form.addRow("属性名称：", self.name_edit)
        form.addRow("值类型：", self.type_combo)
        form.addRow("可选值：", self.options_edit)
        form.addRow("", self.options_hint)
        form.addRow("", self.children_hint)
        splitter.addWidget(right)
        splitter.setSizes([370, 350])

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.btn_add.clicked.connect(self._add_root)
        self.btn_add_child.clicked.connect(self._add_child)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_up.clicked.connect(lambda: self._move_selected(-1))
        self.btn_down.clicked.connect(lambda: self._move_selected(1))
        self.tree.currentItemChanged.connect(self._on_selection_changed)
        self.name_edit.editingFinished.connect(self._apply_editor)
        self.type_combo.currentIndexChanged.connect(self._apply_editor)
        self.options_edit.textChanged.connect(self._apply_editor)

    def get_attributes(self):
        self._apply_editor()
        return deepcopy(self._attributes)

    def _iter_nodes(self, nodes=None, parent_list=None):
        nodes = self._attributes if nodes is None else nodes
        for index, node in enumerate(nodes):
            yield node, nodes, index
            yield from self._iter_nodes(node.get("children", []), node.get("children", []))

    def _node_for_id(self, node_id):
        for node, parent_list, index in self._iter_nodes():
            if node.get("id") == node_id:
                return node, parent_list, index
        return None, None, -1

    def _selected_id(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def _refresh_tree(self, selected_id=None):
        selected_id = selected_id or self._selected_id()
        self.tree.blockSignals(True)
        self.tree.clear()

        def add_nodes(nodes, parent=None):
            for node in nodes:
                item = QTreeWidgetItem(parent or self.tree)
                item.setData(0, Qt.UserRole, node["id"])
                item.setText(0, node["name"])
                item.setText(1, ATTRIBUTE_TYPE_LABELS.get(node["type"], node["type"]))
                add_nodes(node.get("children", []), item)
                if node["id"] == selected_id:
                    self.tree.setCurrentItem(item)

        add_nodes(self._attributes)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._populate_editor()

    def _on_selection_changed(self, _current, _previous):
        self._populate_editor()

    def _populate_editor(self):
        node, _, _ = self._node_for_id(self._selected_id())
        self._updating_editor = True
        enabled = node is not None
        for widget in (self.name_edit, self.type_combo, self.options_edit):
            widget.setEnabled(enabled)
        if node:
            self.name_edit.setText(node.get("name", ""))
            idx = self.type_combo.findData(node.get("type", "single_select"))
            self.type_combo.setCurrentIndex(max(idx, 0))
            self.options_edit.setPlainText("\n".join(node.get("options", [])))
            selectable = node.get("type") in {"single_select", "multi_select"}
            self.options_edit.setVisible(selectable)
            self.options_hint.setVisible(selectable)
        else:
            self.name_edit.clear()
            self.options_edit.clear()
            self.options_edit.setVisible(False)
            self.options_hint.setVisible(False)
        self._updating_editor = False

    def _apply_editor(self):
        if self._updating_editor:
            return
        node, _, _ = self._node_for_id(self._selected_id())
        if not node:
            return
        name = self.name_edit.text().strip()
        if name:
            node["name"] = name
        node["type"] = self.type_combo.currentData()
        node["options"] = list(dict.fromkeys(
            line.strip() for line in self.options_edit.toPlainText().splitlines() if line.strip()
        ))
        item = self.tree.currentItem()
        if item:
            item.setText(0, node["name"])
            item.setText(1, ATTRIBUTE_TYPE_LABELS.get(node["type"], node["type"]))
        selectable = node["type"] in {"single_select", "multi_select"}
        self.options_edit.setVisible(selectable)
        self.options_hint.setVisible(selectable)

    def _ask_name(self, title, default="新属性"):
        name, ok = QInputDialog.getText(self, title, "属性名称：", text=default)
        return name.strip() if ok and name.strip() else None

    def _add_root(self):
        name = self._ask_name("新增属性")
        if not name:
            return
        node = new_attribute_node(name)
        self._attributes.append(node)
        self._refresh_tree(node["id"])

    def _add_child(self):
        parent, _, _ = self._node_for_id(self._selected_id())
        if not parent:
            QMessageBox.information(self, "请选择属性", "请先在左侧选中一个属性，再创建它的子属性。")
            return
        name = self._ask_name("新增子属性")
        if not name:
            return
        node = new_attribute_node(name)
        parent.setdefault("children", []).append(node)
        self._refresh_tree(node["id"])

    def _delete_selected(self):
        node, parent_list, index = self._node_for_id(self._selected_id())
        if not node:
            return
        answer = QMessageBox.question(
            self, "删除属性", f"确定删除“{node['name']}”及其全部子属性吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            parent_list.pop(index)
            self._refresh_tree()

    def _move_selected(self, delta):
        node, parent_list, index = self._node_for_id(self._selected_id())
        target = index + delta
        if not node or target < 0 or target >= len(parent_list):
            return
        parent_list[index], parent_list[target] = parent_list[target], parent_list[index]
        self._refresh_tree(node["id"])


class AnnotationAttributeEditor(QWidget):
    """Render and edit values for the single annotation currently selected."""

    attributes_changed = Signal(object)
    attribute_label_edit_requested = Signal(object, str, str)
    class_label_changed = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shape = None
        self._schemas = {"global": [], "class": []}
        self._building = False
        self._refresh_scheduled = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.title = QLabel("标注属性")
        self.title.setStyleSheet("font-weight: 600;")
        self.class_label_combo = QComboBox()
        self.class_label_combo.setEditable(True)
        self.class_label_combo.setEnabled(False)
        self.class_label_combo.setToolTip("选择已有类别，或直接输入新类别标签")
        class_row = QFormLayout()
        class_row.setContentsMargins(0, 0, 0, 0)
        class_row.addRow("类别标签：", self.class_label_combo)
        self.hint = QLabel("选中一个标注后，可填写该类别定义的属性。")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #64748B; font-size: 11px;")
        layout.addWidget(self.title)
        layout.addLayout(class_row)
        layout.addWidget(self.hint)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.content = QWidget()
        self.form = QFormLayout(self.content)
        self.form.setContentsMargins(2, 4, 2, 4)
        self.form.setSpacing(7)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.class_label_combo.activated.connect(self._emit_class_label_change)
        self.class_label_combo.lineEdit().editingFinished.connect(self._emit_class_label_change)

    def clear(self, hint="选中一个标注后，可填写该类别定义的属性。"):
        self._shape = None
        self._schemas = {"global": [], "class": []}
        self._building = True
        self.class_label_combo.clear()
        self.class_label_combo.setEnabled(False)
        self._building = False
        self.hint.setText(hint)
        self._clear_form()

    def set_annotation(self, shape, global_schema, class_schema, class_labels=()):
        self._shape = shape
        self._schemas = {
            "global": global_schema or [],
            "class": class_schema or [],
        }
        if not hasattr(shape, "attributes") or not isinstance(shape.attributes, dict):
            shape.attributes = {}
        self._building = True
        self.class_label_combo.clear()
        labels = list(dict.fromkeys(label for label in class_labels if label))
        current_label = getattr(shape, "label", "")
        if current_label and current_label not in labels:
            labels.insert(0, current_label)
        self.class_label_combo.addItems(labels)
        self.class_label_combo.setCurrentText(current_label)
        self.class_label_combo.setEnabled(True)
        self._building = False
        self.hint.setText("公共属性适用于全部类别；类别属性仅适用于当前类别。父属性未填写时，子属性会隐藏。")
        self._rebuild_form()

    def _clear_form(self):
        while self.form.rowCount():
            self.form.removeRow(0)

    @staticmethod
    def _has_value(value):
        if isinstance(value, bool):
            # False is an explicit answer ("否"), not an empty value.
            return True
        return value is not None and value != "" and value != []

    def _rebuild_form(self):
        self._building = True
        self._clear_form()
        if not self._shape:
            self._building = False
            return
        global_schema = self._schemas["global"]
        class_schema = self._schemas["class"]
        if global_schema:
            self.form.addRow(self._scope_button("global", "公共属性（全部类别）", global_schema))
        if class_schema:
            self.form.addRow(self._scope_button("class", "类别属性（仅当前类别）", class_schema))
        if not global_schema and not class_schema:
            note = QLabel("当前类别尚未配置属性。点击类别区域的“属性”进行配置。")
            note.setWordWrap(True)
            note.setStyleSheet("color: #64748B;")
            self.form.addRow(note)
        self._building = False

    def _scope_button(self, scope, title, nodes):
        values = self._shape.attributes if self._shape else {}
        node_ids = list(self._descendant_ids(nodes))
        selected_count = sum(1 for node_id in node_ids if self._has_value(values.get(node_id)))
        button = QPushButton(f"{title}  ·  已填写 {selected_count}/{len(node_ids)} 项  ›")
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip("点击查看和修改该范围内的全部属性")
        button.setStyleSheet("""
            QPushButton {
                min-height: 32px;
                padding: 5px 9px;
                border: 1px solid #94A3B8;
                border-radius: 6px;
                background: transparent;
                font-weight: 600;
                text-align: left;
            }
            QPushButton:hover { border-color: #3B82F6; background: rgba(59, 130, 246, 0.10); }
        """)
        button.clicked.connect(lambda: self._open_scope_dialog(scope, title))
        return button

    def _open_scope_dialog(self, scope, title):
        dialog = AttributeScopeDialog(self, scope, title, self)
        dialog.exec()

    def _add_nodes(self, nodes, scope, depth=0):
        values = self._shape.attributes
        for node in nodes:
            node_id = node.get("id")
            if not node_id:
                continue
            value = values.get(node_id)
            label = EditableAttributeLabel(node_id, ("　" * depth) + node.get("name", "属性"))
            label.edit_requested.connect(self._request_label_edit)
            widget = self._make_value_widget(node, value, scope)
            self.form.addRow(label, widget)
            if self._has_value(value):
                self._add_nodes(node.get("children", []), scope, depth + 1)

    def _make_value_widget(self, node, value, scope):
        node_id = node["id"]
        node_type = node.get("type", "single_select")
        if node_type == "single_select":
            options = node.get("options", [])
            if options:
                widget = AttributeValuePickerButton(node.get("name", "属性"), options, value, multiple=False)
                widget.value_changed.connect(
                    lambda selected, key=node_id, current_scope=scope:
                    self._set_value(key, selected, current_scope)
                )
            else:
                # A newly-created select attribute has no choices yet.  Keep it
                # usable by accepting a free value until choices are configured.
                widget = QLineEdit(str(value) if value is not None else "")
                widget.setPlaceholderText("请输入属性值（可在属性管理中配置选项）")
                widget.editingFinished.connect(
                    lambda key=node_id, current_scope=scope, w=widget:
                    self._set_value(key, w.text().strip(), current_scope)
                )
            return widget
        if node_type == "multi_select":
            if not node.get("options", []):
                widget = QLineEdit(", ".join(value) if isinstance(value, list) else "")
                widget.setPlaceholderText("多个值用逗号分隔（可在属性管理中配置选项）")
                widget.editingFinished.connect(
                    lambda key=node_id, current_scope=scope, w=widget: self._set_value(
                        key,
                        list(dict.fromkeys(part.strip() for part in w.text().split(",") if part.strip())),
                        current_scope,
                    )
                )
                return widget
            widget = AttributeValuePickerButton(
                node.get("name", "属性"), node.get("options", []), value, multiple=True
            )
            widget.value_changed.connect(
                lambda selected, key=node_id, current_scope=scope:
                self._set_value(key, selected, current_scope)
            )
            return widget
        if node_type == "boolean":
            widget = BooleanValuePicker(value)
            widget.value_changed.connect(
                lambda selected, key=node_id, current_scope=scope:
                self._set_value(key, selected, current_scope)
            )
            return widget
        if node_type == "number":
            # A line edit keeps "not filled" distinct from 0, which a spin box
            # cannot represent without introducing a magic sentinel value.
            widget = QLineEdit(str(value) if isinstance(value, (int, float)) else "")
            widget.setPlaceholderText("请输入数值")
            widget.setValidator(QDoubleValidator(-1_000_000_000, 1_000_000_000, 6, widget))
            widget.editingFinished.connect(
                lambda key=node_id, current_scope=scope, w=widget: self._set_number_value(key, w.text(), current_scope)
            )
            return widget

        widget = QLineEdit(str(value) if value is not None else "")
        widget.editingFinished.connect(
            lambda key=node_id, current_scope=scope, w=widget: self._set_value(key, w.text().strip(), current_scope)
        )
        return widget

    def _descendant_ids(self, nodes):
        for node in nodes:
            yield node.get("id")
            yield from self._descendant_ids(node.get("children", []))

    def _find_node(self, node_id, scope, nodes=None):
        nodes = self._schemas.get(scope, []) if nodes is None else nodes
        for node in nodes:
            if node.get("id") == node_id:
                return node
            found = self._find_node(node_id, scope, node.get("children", []))
            if found:
                return found
        return None

    def _set_value(self, node_id, value, scope):
        if self._building or not self._shape:
            return
        values = self._shape.attributes
        node = self._find_node(node_id, scope)
        previously_active = self._has_value(values.get(node_id))
        if value in (None, "", []):
            values.pop(node_id, None)
            if node:
                for descendant_id in self._descendant_ids(node.get("children", [])):
                    values.pop(descendant_id, None)
        else:
            values[node_id] = value
        self.attributes_changed.emit(self._shape)
        # Rebuilding synchronously here deletes the sender while Qt is still
        # dispatching its signal, which can crash PySide6.  A redraw is only
        # needed when a parent changes between empty and non-empty states.
        if node and node.get("children") and previously_active != self._has_value(values.get(node_id)):
            self._schedule_rebuild()
        else:
            self._schedule_summary_refresh()

    def _request_label_edit(self, node_id):
        if self._shape:
            for scope in ("global", "class"):
                if self._find_node(node_id, scope):
                    self.attribute_label_edit_requested.emit(self._shape, node_id, scope)
                    return

    def _emit_class_label_change(self):
        if self._building or not self._shape:
            return
        new_label = self.class_label_combo.currentText().strip()
        if new_label and new_label != getattr(self._shape, "label", ""):
            self.class_label_changed.emit(self._shape, new_label)

    def _set_number_value(self, node_id, text, scope):
        text = text.strip()
        if not text:
            self._set_value(node_id, "", scope)
            return
        try:
            self._set_value(node_id, float(text), scope)
        except ValueError:
            # QDoubleValidator normally prevents this path; retaining the
            # previous value is safer than coercing an invalid value to zero.
            return

    def _schedule_rebuild(self):
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True

        def rebuild():
            self._refresh_scheduled = False
            if self._shape:
                self._rebuild_form()

        QTimer.singleShot(0, rebuild)

    def _schedule_summary_refresh(self):
        if self._refresh_scheduled:
            return
        self._refresh_scheduled = True

        def rebuild():
            self._refresh_scheduled = False
            if self._shape:
                self._rebuild_form()

        QTimer.singleShot(0, rebuild)
