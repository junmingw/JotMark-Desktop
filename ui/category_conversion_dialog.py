"""Safe, copy-on-write category and attribute conversion dialog."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import traceback

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from jotmark.data.category_conversion import (
    ConversionError,
    attributes_for_class,
    convert_attribute_to_category,
    convert_category_to_attribute,
    convert_category_to_category,
    discover_attribute_values,
    load_project_metadata,
    safe_category_suffix,
)


MODE_CATEGORY_TO_CATEGORY = "类别 → 类别"
MODE_ATTRIBUTE_TO_CATEGORY = "属性 → 类别"
MODE_CATEGORY_TO_ATTRIBUTE = "类别 → 属性"


class ConversionWorker(QThread):
    """Run file copying on a worker thread so the safety dialog stays usable."""

    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, function, kwargs, parent=None):
        super().__init__(parent)
        self.function = function
        self.kwargs = kwargs

    def run(self):
        try:
            self.completed.emit(self.function(**self.kwargs))
        except Exception as exc:  # Keep conversion errors readable in the UI.
            detail = str(exc) or exc.__class__.__name__
            if not isinstance(exc, ConversionError):
                detail = f"{detail}\n\n{traceback.format_exc()}"
            self.failed.emit(detail)


class CategoryConversionDialog(QDialog):
    """Convert project labels and attributes without modifying the source."""

    def __init__(self, source_directory="", selected_class="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("类别 / 属性安全转换")
        self.resize(780, 700)
        self.setModal(True)
        self._metadata = None
        self._selected_class = selected_class or ""
        self._worker = None
        self._build_ui()
        if source_directory:
            self.source_edit.setText(os.path.abspath(source_directory))
        self._reload_project()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "此工具不会修改源项目：先对源目录创建完整快照，再将转换后的完整项目写到一个全新的目录。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-weight: 600;")
        layout.addWidget(intro)

        paths = QFrame()
        paths.setFrameShape(QFrame.StyledPanel)
        path_form = QFormLayout(paths)
        path_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("选择包含图片、JSON、classes.txt 的源项目目录")
        self.source_edit.editingFinished.connect(self._reload_project)
        source_row = self._with_button(self.source_edit, "选择…", self._choose_source)
        path_form.addRow("源项目目录：", source_row)

        self.output_parent_edit = QLineEdit()
        self.output_parent_edit.editingFinished.connect(self._update_paths)
        output_parent_row = self._with_button(self.output_parent_edit, "选择…", self._choose_output_parent)
        path_form.addRow("输出父目录：", output_parent_row)

        self.output_name_edit = QLineEdit()
        self.output_name_edit.setPlaceholderText("例如：my_dataset-converted")
        self.output_name_edit.textChanged.connect(self._update_paths)
        path_form.addRow("新输出文件夹名：", self.output_name_edit)

        self.output_preview = QLabel("—")
        self.output_preview.setWordWrap(True)
        self.output_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_form.addRow("转换结果目录：", self.output_preview)
        self.backup_preview = QLabel("—")
        self.backup_preview.setWordWrap(True)
        self.backup_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_form.addRow("自动备份目录：", self.backup_preview)
        layout.addWidget(paths)

        self.project_status = QLabel("请选择源项目目录。")
        self.project_status.setWordWrap(True)
        layout.addWidget(self.project_status)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("转换方式："))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems((
            MODE_CATEGORY_TO_CATEGORY,
            MODE_ATTRIBUTE_TO_CATEGORY,
            MODE_CATEGORY_TO_ATTRIBUTE,
        ))
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self.mode_combo, 1)
        layout.addLayout(mode_row)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_category_to_category_page())
        self.pages.addWidget(self._build_attribute_to_category_page())
        self.pages.addWidget(self._build_category_to_attribute_page())
        layout.addWidget(self.pages, 1)

        self.result_box = QPlainTextEdit()
        self.result_box.setReadOnly(True)
        self.result_box.setVisible(False)
        self.result_box.setMaximumHeight(120)
        layout.addWidget(self.result_box)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.convert_button = self.buttons.addButton("创建备份并转换", QDialogButtonBox.AcceptRole)
        self.convert_button.clicked.connect(self._confirm_and_convert)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @staticmethod
    def _with_button(widget, label, callback):
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(widget, 1)
        button = QPushButton(label)
        button.clicked.connect(callback)
        row.addWidget(button)
        return container

    def _build_category_to_category_page(self):
        page = QWidget()
        form = QFormLayout(page)
        self.c2c_source = QComboBox()
        self.c2c_target = QComboBox()
        self.c2c_target.setEditable(True)
        self.c2c_keep_attributes = QCheckBox("仅保留与目标类别/公共属性兼容的属性值")
        self.c2c_keep_attributes.setChecked(True)
        self.c2c_remove_source = QCheckBox("转换后从类别列表移除源类别")
        self.c2c_remove_source.setChecked(True)
        form.addRow("源类别：", self.c2c_source)
        form.addRow("目标类别：", self.c2c_target)
        form.addRow("属性处理：", self.c2c_keep_attributes)
        form.addRow("类别处理：", self.c2c_remove_source)
        hint = QLabel("会把所有源类别标注改为目标类别；输出中仅保留目标类别能够识别的属性。")
        hint.setWordWrap(True)
        form.addRow("说明：", hint)
        return page

    def _build_attribute_to_category_page(self):
        page = QWidget()
        form = QFormLayout(page)
        self.a2c_source = QComboBox()
        self.a2c_attribute = QComboBox()
        self.a2c_prefix = QLineEdit()
        self.a2c_prefix.setPlaceholderText("例如 boundary，结果为 boundary_curb")
        self.a2c_remove_attribute = QCheckBox("从新类别的属性定义和标注中移除已提升属性")
        self.a2c_remove_source = QCheckBox("若所有源类别标注均已转换，则移除源类别")
        self.a2c_remove_source.setChecked(True)
        self.a2c_mapping = QPlainTextEdit()
        self.a2c_mapping.setReadOnly(True)
        self.a2c_mapping.setMinimumHeight(120)
        self.a2c_source.currentIndexChanged.connect(self._update_attribute_candidates)
        self.a2c_attribute.currentIndexChanged.connect(self._update_attribute_mapping)
        self.a2c_attribute.currentIndexChanged.connect(
            lambda _index: self._on_remove_lifted_toggled(self.a2c_remove_attribute.isChecked())
        )
        self.a2c_prefix.textChanged.connect(self._update_attribute_mapping)
        self.a2c_remove_attribute.toggled.connect(self._on_remove_lifted_toggled)
        form.addRow("源类别：", self.a2c_source)
        form.addRow("具体属性：", self.a2c_attribute)
        form.addRow("新类别前缀：", self.a2c_prefix)
        form.addRow("属性处理：", self.a2c_remove_attribute)
        form.addRow("类别处理：", self.a2c_remove_source)
        form.addRow("预览映射：", self.a2c_mapping)
        return page

    def _build_category_to_attribute_page(self):
        page = QWidget()
        form = QFormLayout(page)
        self.c2a_source = QComboBox()
        self.c2a_target = QComboBox()
        self.c2a_target.setEditable(True)
        self.c2a_attribute = QComboBox()
        self.c2a_new_attribute_name = QLineEdit()
        self.c2a_new_attribute_name.setPlaceholderText("没有合适属性时，填写新属性名称")
        self.c2a_value = QLineEdit()
        self.c2a_value.setPlaceholderText("例如 curb")
        self.c2a_remove_source = QCheckBox("转换后从类别列表移除源类别")
        self.c2a_remove_source.setChecked(True)
        self.c2a_target.currentIndexChanged.connect(self._update_target_attribute_candidates)
        self.c2a_target.editTextChanged.connect(self._update_target_attribute_candidates)
        self.c2a_attribute.currentIndexChanged.connect(self._on_target_attribute_changed)
        form.addRow("源类别：", self.c2a_source)
        form.addRow("目标类别：", self.c2a_target)
        form.addRow("写入属性：", self.c2a_attribute)
        form.addRow("新属性名称：", self.c2a_new_attribute_name)
        form.addRow("属性值：", self.c2a_value)
        form.addRow("类别处理：", self.c2a_remove_source)
        hint = QLabel("例如将 boundary_curb 转为 boundary，并写入 boundary_kind = curb。")
        hint.setWordWrap(True)
        form.addRow("说明：", hint)
        return page

    def _choose_source(self):
        directory = QFileDialog.getExistingDirectory(self, "选择源标注项目目录", self.source_edit.text() or "")
        if directory:
            self.source_edit.setText(directory)
            self._reload_project()

    def _choose_output_parent(self):
        directory = QFileDialog.getExistingDirectory(
            self, "选择转换结果的父目录", self.output_parent_edit.text() or self.source_edit.text() or ""
        )
        if directory:
            self.output_parent_edit.setText(directory)
            self._update_paths()

    def _reload_project(self):
        path = self.source_edit.text().strip()
        self._metadata = None
        self._clear_combos()
        if not path:
            self.project_status.setText("请选择源项目目录。")
            self._update_paths()
            return
        try:
            self._metadata = load_project_metadata(path)
        except ConversionError as exc:
            self.project_status.setText(f"无法读取源项目：{exc}")
            self._update_paths()
            return
        source = self._metadata["source"]
        if not self.output_parent_edit.text().strip():
            self.output_parent_edit.setText(str(source.parent))
        if not self.output_name_edit.text().strip():
            self.output_name_edit.setText(
                f"{source.name}-converted-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            )
        classes = self._metadata["classes"]
        count = sum(
            len(document.get("shapes", []))
            for document in self._metadata["documents"].values()
            if isinstance(document, dict)
        )
        self.project_status.setText(
            f"已读取：{len(classes)} 个类别、{len(self._metadata['files'])} 个 JSON 标注文件、{count} 个标注。"
        )
        self._populate_class_combos(classes)
        self._update_paths()

    def _clear_combos(self):
        for combo in (
            self.c2c_source, self.c2c_target, self.a2c_source,
            self.a2c_attribute, self.c2a_source, self.c2a_target,
            self.c2a_attribute,
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)
        self.a2c_mapping.clear()

    def _populate_class_combos(self, classes):
        for combo in (self.c2c_source, self.a2c_source, self.c2a_source):
            combo.addItems(classes)
            if self._selected_class and self._selected_class in classes:
                combo.setCurrentText(self._selected_class)
        for combo in (self.c2c_target, self.c2a_target):
            combo.addItems(classes)
        self._update_attribute_candidates()
        self._update_target_attribute_candidates()

    @staticmethod
    def _attribute_text(attribute, scope):
        type_names = {
            "single_select": "单选",
            "multi_select": "多选",
            "text": "文本",
            "number": "数值",
            "boolean": "是/否",
        }
        scope_name = "公共" if scope == "global" else "类别"
        return f"[{scope_name} · {type_names.get(attribute.get('type'), '单选')}] {attribute['path']}"

    def _update_attribute_candidates(self, *_args):
        self.a2c_attribute.blockSignals(True)
        self.a2c_attribute.clear()
        if self._metadata:
            class_name = self.a2c_source.currentText().strip()
            schema = self._metadata["schema"]
            global_ids = {attribute["id"] for attribute in attributes_for_class(schema, "", include_global=True)}
            for attribute in attributes_for_class(schema, class_name):
                if attribute.get("type") == "multi_select":
                    continue
                scope = "global" if attribute["id"] in global_ids else "class"
                self.a2c_attribute.addItem(self._attribute_text(attribute, scope), (attribute["id"], scope))
        self.a2c_attribute.blockSignals(False)
        self._on_remove_lifted_toggled(self.a2c_remove_attribute.isChecked())
        self._update_attribute_mapping()

    def _on_remove_lifted_toggled(self, checked):
        data = self.a2c_attribute.currentData()
        is_global = bool(data and data[1] == "global")
        self.a2c_remove_attribute.setEnabled(not is_global)
        if is_global and checked:
            self.a2c_remove_attribute.setChecked(False)
            self.a2c_remove_attribute.setToolTip("公共属性还会被其他类别使用，因此不能在这里移除。")
        else:
            self.a2c_remove_attribute.setToolTip("")

    def _update_attribute_mapping(self, *_args):
        self.a2c_mapping.clear()
        if not self._metadata:
            return
        data = self.a2c_attribute.currentData()
        source_class = self.a2c_source.currentText().strip()
        if not data or not source_class:
            self.a2c_mapping.setPlainText("请选择源类别和一个单值属性。")
            return
        values = discover_attribute_values(self._metadata, source_class, data[0])
        prefix = self.a2c_prefix.text().strip() or "<前缀>"
        if not values:
            self.a2c_mapping.setPlainText("该属性在当前源类别的标注中尚未填写值。")
            return
        lines = [
            f"{value}（{count} 个标注） → {prefix}_{safe_category_suffix(value)}"
            for value, count in sorted(values.items())
        ]
        self.a2c_mapping.setPlainText("\n".join(lines))

    def _update_target_attribute_candidates(self, *_args):
        current = self.c2a_attribute.currentData()
        self.c2a_attribute.blockSignals(True)
        self.c2a_attribute.clear()
        self.c2a_attribute.addItem("新建单选属性…", "")
        if self._metadata:
            target = self.c2a_target.currentText().strip()
            schema = self._metadata["schema"]
            global_ids = {attribute["id"] for attribute in attributes_for_class(schema, "", include_global=True)}
            for attribute in attributes_for_class(schema, target):
                if attribute.get("type") == "multi_select":
                    continue
                scope = "global" if attribute["id"] in global_ids else "class"
                self.c2a_attribute.addItem(self._attribute_text(attribute, scope), attribute["id"])
        if current:
            index = self.c2a_attribute.findData(current)
            if index >= 0:
                self.c2a_attribute.setCurrentIndex(index)
        self.c2a_attribute.blockSignals(False)
        self._on_target_attribute_changed()

    def _on_target_attribute_changed(self, *_args):
        is_new = not bool(self.c2a_attribute.currentData())
        self.c2a_new_attribute_name.setEnabled(is_new)
        if not is_new:
            self.c2a_new_attribute_name.clear()

    def _update_paths(self, *_args):
        source_text = self.source_edit.text().strip()
        parent_text = self.output_parent_edit.text().strip()
        name = self.output_name_edit.text().strip()
        output = Path(parent_text).expanduser() / name if parent_text and name else None
        self.output_preview.setText(str(output) if output else "请先选择输出父目录并填写新文件夹名")
        if source_text:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.backup_preview.setText(str(Path(source_text).expanduser() / ".jotmark_backups" / f"category-conversion-{stamp}"))
        else:
            self.backup_preview.setText("—")

    def _on_mode_changed(self, index):
        self.pages.setCurrentIndex(index)

    def _conversion_call(self):
        if not self._metadata:
            raise ConversionError("请先选择可读取的源项目目录。")
        source = self.source_edit.text().strip()
        parent = self.output_parent_edit.text().strip()
        name = self.output_name_edit.text().strip()
        if not parent or not name or name in {".", ".."} or Path(name).name != name:
            raise ConversionError("请填写不含路径分隔符的新输出文件夹名。")
        output = str(Path(parent).expanduser() / name)
        mode = self.mode_combo.currentIndex()
        if mode == 0:
            return convert_category_to_category, {
                "source_dir": source, "output_dir": output,
                "source_class": self.c2c_source.currentText().strip(),
                "target_class": self.c2c_target.currentText().strip(),
                "keep_compatible_attributes": self.c2c_keep_attributes.isChecked(),
                "remove_source_category": self.c2c_remove_source.isChecked(),
            }
        if mode == 1:
            attr_data = self.a2c_attribute.currentData()
            return convert_attribute_to_category, {
                "source_dir": source, "output_dir": output,
                "source_class": self.a2c_source.currentText().strip(),
                "attribute_id": attr_data[0] if attr_data else "",
                "category_prefix": self.a2c_prefix.text().strip(),
                "remove_lifted_attribute": self.a2c_remove_attribute.isChecked(),
                "remove_source_category": self.a2c_remove_source.isChecked(),
            }
        return convert_category_to_attribute, {
            "source_dir": source, "output_dir": output,
            "source_class": self.c2a_source.currentText().strip(),
            "target_class": self.c2a_target.currentText().strip(),
            "attribute_id": self.c2a_attribute.currentData() or "",
            "attribute_name": self.c2a_new_attribute_name.text().strip(),
            "attribute_value": self.c2a_value.text().strip(),
            "remove_source_category": self.c2a_remove_source.isChecked(),
        }

    def _confirm_and_convert(self):
        try:
            function, kwargs = self._conversion_call()
            # Ask the service to validate risky path rules before the user
            # confirms a potentially lengthy full-project backup.
            source = Path(kwargs["source_dir"]).expanduser().resolve()
            output = Path(kwargs["output_dir"]).expanduser().resolve()
            if output == source or output.exists() or source in output.parents or output in source.parents:
                raise ConversionError("输出必须是源项目外一个尚不存在的新目录。")
        except ConversionError as exc:
            QMessageBox.warning(self, "无法开始转换", str(exc))
            return
        answer = QMessageBox(self)
        answer.setWindowTitle("确认创建备份并转换")
        answer.setIcon(QMessageBox.Warning)
        answer.setText("将创建完整备份，并写入一个新的转换项目。")
        answer.setInformativeText(
            f"备份：\n{self.backup_preview.text()}\n\n"
            f"新项目：\n{kwargs['output_dir']}\n\n"
            "源项目不会被修改。是否继续？"
        )
        proceed = answer.addButton("创建备份并转换", QMessageBox.AcceptRole)
        answer.addButton("取消", QMessageBox.RejectRole)
        answer.exec()
        if answer.clickedButton() != proceed:
            return
        self.convert_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Cancel).setEnabled(False)
        self.result_box.setVisible(True)
        self.result_box.setPlainText("正在创建完整备份并生成新项目。项目较大时请耐心等待；请不要关闭窗口。")
        self._worker = ConversionWorker(function, kwargs, self)
        self._worker.completed.connect(self._on_completed)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_completed(self, result):
        self.convert_button.setEnabled(True)
        self.buttons.button(QDialogButtonBox.Cancel).setEnabled(True)
        self.result_box.setPlainText(
            "转换完成。\n"
            f"已转换标注：{result.get('converted_shapes', 0)}\n"
            f"完整备份：{result['backup_directory']}\n"
            f"新项目：{result['output_directory']}\n\n"
            "请通过主界面的“打开目录”选择新项目目录后继续使用。"
        )
        QMessageBox.information(
            self, "转换完成",
            f"已转换 {result.get('converted_shapes', 0)} 个标注。\n\n"
            f"备份：{result['backup_directory']}\n"
            f"新项目：{result['output_directory']}\n\n"
            "源项目未被修改。"
        )

    def _on_failed(self, message):
        self.convert_button.setEnabled(True)
        self.buttons.button(QDialogButtonBox.Cancel).setEnabled(True)
        self.result_box.setPlainText(f"转换失败：\n{message}")
        QMessageBox.critical(self, "转换失败", message)

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "转换进行中", "正在复制文件。为保护备份和输出项目，转换完成前不能关闭此窗口。")
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "转换进行中", "正在复制文件。转换完成前不能取消或关闭此窗口。")
            return
        super().reject()
