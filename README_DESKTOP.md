# JotMark Desktop Workspace

这是从 JotMark 主项目拆出的桌面端工作空间，仅包含本地标注桌面程序及其内嵌训练平台所需源码。

## 已包含

- `main.py`：桌面应用入口；
- `jotmark/`：标注画布、导入导出、SAM2/SAM3 与 YOLO 辅助逻辑；
- `ui/`：桌面标注界面与图标；
- `utils/`：桌面通知组件；
- `TrainingPlatform/`：桌面内嵌训练平台的运行代码与配置；
- `weights/sam_weights/sam3.pt`：SAM3 本地模型权重实体副本；
- `scripts/run_desktop.sh`：桌面启动脚本；
- `requirements-core.txt`、`requirements-ai.txt`：依赖清单。

## 未包含

- `webapp/`、在线协作服务和数据库；
- Docker Compose 配置；
- 汇报材料、测试图片、在线数据与虚拟环境；
- TrainingPlatform 的测试、开发工具和示例图片。

## 启动

当前验证复用原工作区已有环境：

```bash
cd ~/JotMark-Desktop
ENV_DIR=/home/nex/Documents/JotMark/.venv-labelpaw ./scripts/run_desktop.sh
```

若之后创建了本工作区自己的 `.venv-jotmark`，可直接执行：

```bash
cd ~/JotMark-Desktop
./scripts/run_desktop.sh
```

SAM3 已放在 `weights/sam_weights/sam3.pt`，桌面端会自动发现该模型；不再依赖
`/home/nex/Downloads/sam3/sam3.pt`。该文件是实体副本，不是软链接。
