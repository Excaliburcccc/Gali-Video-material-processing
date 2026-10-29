# Gali Video Material Processing

Windows 本地游戏录像检索与 OBS 快捷标记工具，按《影之刃零》的地图收集、任务、读物和 BOSS 建立目录。

## 功能

- 地图收集：铃铛、武器及影之武装、饰品及药品、挑战地点、杀气琥珀碎片、黑石碎片、记忆之花。
- 任务：故事、异闻、收集。读物：书籍、记忆之花。另有 BOSS 分类，均支持名称搜索并跳转播放。
- 默认只自动保留以上类目；不再收录普通字幕、教程和菜单说明。多文字界面保留有类别证据的当前标题，同一录像内同名对象只保留首次位置；强化回退材料不进入目录。
- OBS 录制期间使用 `Ctrl+Alt+1/2/3/4` 添加收集、击败、成就和路线标记。
- 生成轻量预览，导出 H.264/AAC 片段供剪映导入，导出 CSV 事件目录。
- 使用本地 RapidOCR PP-OCRv3 / ONNX Runtime，不上传录像，不调用在线识别 API。

自动结果需要核实。首次看到不等于获得或击败；地图选中不等于激活传送点。当前规则针对演示中的 16:9 SDR 界面，正式版或其他界面需重新校准。

## 安装与启动

环境：Windows、64 位 Python 3.13（带 tkinter）、FFmpeg 与 ffprobe。将 FFmpeg 的 `bin` 目录加入 PATH。

所有项目、临时文件和可配置缓存默认放在 `E:\CodexFiles`。本机现有工作目录为 `E:\CodexFiles\影之刃零素材助手`。

```powershell
git clone https://github.com/Excaliburcccc/Gali-Video-material-processing.git E:\CodexFiles\Gali-Video-material-processing
Set-Location E:\CodexFiles\Gali-Video-material-processing
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

首次安装下载 Python 依赖；本地 OCR 模型随依赖安装。安装完成后双击 `启动素材助手.cmd`，访问 `http://127.0.0.1:8877`。运行 `setup.ps1 -CheckOnly` 可检查环境。退出后台服务使用 `停止素材助手.cmd`。

“识别设置”默认使用《影之刃零》指定类目识别，可调整采样间隔与识别宽度。完整操作说明见 [使用说明.txt](使用说明.txt)。

## OBS 连接

新安装不会自动修改 OBS。纯素材检索不要求连接 OBS。

1. 在本机 OBS 的 WebSocket 服务器设置中启用服务器和密码验证，默认端口 4455。
2. 复制 `config/obs-connection.example.json` 为 `data/obs-connection.json`，填入本机端口和密码。此文件已被 Git 排除，不要提交密码。
3. 先启动助手，再开始录制。正常结束录制后，录像与标记进入素材库。录制途中启动或重连无法保证计时，须从下一段开始使用快捷标记。

原有电脑的 OBS 配置、原片和索引继续保留。新机器需自行配置录制场景、编码、保存路径与声音；建议使用支持章节的录制格式，不启用自动分段。

## 文件与备份

| 内容 | 位置 | Git 同步 |
| --- | --- | --- |
| 后端与识别规则 | `app/` | 是 |
| 网页界面 | `static/` | 是 |
| 必需胜利字形模板 | `app/assets/` | 是 |
| 测试源码 | `tests/test_*.py` | 是 |
| 本地索引、截图、OBS 连接与备份 | `data/` | 否 |
| 录像、导出片段 | `recordings/`、`exports/` | 否 |
| Python 环境与 OCR 模型 | `.venv/` | 否 |

仓库保存代码版本，不能替代录像和素材库备份。重要本地资料需另外备份；备份 SQLite 索引前先退出服务。

## 开发与测试

后续开发以此私有仓库为准。开始前检查工作区和远端，修改后运行相应测试、提交并推送。不要提交运行数据，也不要强制覆盖远端历史。具体约定见 [AGENTS.md](AGENTS.md)。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test.ps1
```

测试使用独立临时素材库，不接触真实索引。真实演示图片没有打包，依赖这些图片的测试会跳过；完整 OCR 合成录像测试可在本机设置 `RUN_OCR_INTEGRATION=1` 后运行。

项目监听本机地址。普通无文字击杀、过短的提示、未验证的成就界面和界面变化仍可能漏检，可用快捷标记补充。
