"""Grouped, application-wide annotation display settings."""

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QVBoxLayout,
)


class SettingsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("标注设置")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)

        annotation = QGroupBox("标注显示")
        form = QFormLayout(annotation)
        self.line_width = QDoubleSpinBox()
        self.line_width.setRange(0.5, 12.0)
        self.line_width.setSingleStep(0.5)
        self.line_width.setDecimals(1)
        self.line_width.setSuffix(" px")
        self.line_width.setValue(window.scene.annotation_line_width)
        self.line_width.setToolTip("调整画布上标注的显示线宽；不改变保存的坐标或导出格式")
        form.addRow("标注线宽", self.line_width)
        self.canvas_attributes = QCheckBox("在画布标签中显示已填写属性（◇ 公共 / ◆ 类别）")
        self.canvas_attributes.setChecked(window.scene.show_canvas_attributes)
        self.canvas_attributes.setToolTip("类别始终显示；属性过多时画布显示摘要，悬停可查看完整值")
        form.addRow(self.canvas_attributes)
        self.transparent_label_background = QCheckBox("画布类别和属性标签使用透明背景")
        self.transparent_label_background.setChecked(window.scene.transparent_label_background)
        self.transparent_label_background.setToolTip("移除标签背景，保留明亮文字并添加深色阴影；不影响标签点击或标注数据")
        form.addRow(self.transparent_label_background)
        self.edge_click_margin = QSpinBox()
        self.edge_click_margin.setRange(0, 160)
        self.edge_click_margin.setSingleStep(10)
        self.edge_click_margin.setSuffix(" px")
        self.edge_click_margin.setValue(window.scene.edge_click_margin)
        self.edge_click_margin.setToolTip(
            "适应窗口时图片四周预留的宽度；无论是否放大或滚动，"
            "在当前可见画布中距图片边缘此距离内点击多边形/折线，"
            "落点都会投影到图片边缘。0 表示关闭。"
        )
        form.addRow("图片边缘留白/投点距离", self.edge_click_margin)
        form.addRow("新标注类别", QLabel("优先右侧当前类别，否则沿用上次标注类别"))
        layout.addWidget(annotation)

        files = QGroupBox("文件列表")
        file_form = QFormLayout(files)
        self.extensions = QCheckBox("显示 .jpg / .png 等扩展名")
        self.extensions.setChecked(window.show_file_extensions)
        file_form.addRow(self.extensions)
        self.sort_key = QComboBox()
        for title, key in (("文件名", "name"), ("修改时间", "modified"), ("文件大小", "size")):
            self.sort_key.addItem(title, key)
        self.sort_key.setCurrentIndex(max(0, self.sort_key.findData(window.file_sort_key)))
        file_form.addRow("排序方式", self.sort_key)
        self.descending = QCheckBox("降序")
        self.descending.setChecked(window.file_sort_descending)
        file_form.addRow(self.descending)
        layout.addWidget(files)

        appearance = QGroupBox("外观")
        appearance_form = QFormLayout(appearance)
        self.dark_theme = QCheckBox("深色主题")
        self.dark_theme.setChecked(window.is_dark_theme)
        appearance_form.addRow(self.dark_theme)
        layout.addWidget(appearance)

        storage = QGroupBox("保存")
        storage_form = QFormLayout(storage)
        output_row = QHBoxLayout()
        self.output_dir = QLineEdit(window.annotation_output_dir or "")
        self.output_dir.setReadOnly(True)
        self.output_dir.setPlaceholderText("图片同目录（默认）")
        choose = QPushButton("选择…")
        choose.clicked.connect(lambda: self._choose_output_dir(window))
        reset = QPushButton("图片同目录")
        reset.setToolTip("恢复默认：标注与图片保存在同一目录")
        reset.clicked.connect(self.output_dir.clear)
        output_row.addWidget(self.output_dir, 1)
        output_row.addWidget(choose)
        output_row.addWidget(reset)
        storage_form.addRow("标注保存目录", output_row)
        layout.addWidget(storage)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_output_dir(self, window):
        directory = QFileDialog.getExistingDirectory(
            self, "选择标注保存目录", self.output_dir.text() or window.current_dir or ""
        )
        if directory:
            self.output_dir.setText(directory)
