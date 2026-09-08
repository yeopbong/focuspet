# Focus Pet 中文使用说明

Focus Pet 是一个安静的像素桌面伙伴，根据本机活动统计估计工作状态，并累计“工作节奏负荷”。它不评价勤奋程度，不测量工作产出，也不作医学疲劳诊断。

## 安装与启动

原生目标平台为 macOS 14+、Apple Silicon arm64、Python 3.11。其他系统的真实采集不标记为通过。源码构建产物位于 `dist/Focus Pet.app`；本轮实际验证、签名与发布状态见 [平台状态](platform-status.md)。

源码方式，在项目根目录执行：

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-macos-arm64.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/focuspet doctor
.venv/bin/focuspet run
```

使用演示，不申请系统输入权限：

```sh
.venv/bin/focuspet demo
```

首次启动选择四位成年角色之一，选择 Coding、Research / Reading、Office / Writing、Creative 或 Mixed 画像，然后阅读统计采集说明并自主授权。画像仅是可随时更改的初始先验。拒绝采集后仍可进入演示。macOS 输入监听需要系统设置中的额外授权；授权状态按信号独立显示，不会反复弹出权限请求。

## 日常操作

- 单击角色：短暂回应。
- 双击：状态卡，显示工作状态、Focus、Work Load、来源、覆盖率、不确定性和实际观测依据。
- 拖动：调整位置；松开不应触发单击。角色位置和整数缩放倍率可保存。
- 右键：休息、延后提醒、安静模式、选择角色、设置、隐藏和退出。
- 菜单栏托盘：打开状态、恢复角色、暂停/继续采集、关闭完全点击穿透、退出。

透明区域采用角色遮罩。完全点击穿透开启后，用托盘恢复交互。自动动画和气泡不会主动夺取键盘焦点。主动打开的设置和状态窗口可正常输入。

第一次有效窗口后即可显示初步结果。`Generic prior` 是可读规则先验，不是人群预训练模型；`Personal model` 需要本人反馈与后续验证。低输入阅读或浏览器活动无法可靠分辨时允许显示 Unknown。休息、暂停、缺失时 Focus 显示“—”。

Work Load 是连续指数：0 为新工作周期起点，100 为固定产品参考线，120 以上主显示为 120+。负数是恢复缓冲，非真实“负疲劳”。点击休息后负荷逐渐降低；跨日、锁屏、无输入和离线不会自动清零。普通任务切换和结束休息不重置。重启后原值标记过期，可继续旧周期、明确确认限定时长的休息，或主动开始新周期。

## 纠正与学习

在状态卡纠正最近 1 / 5 / 15 分钟，默认 5 分钟；也可以在 Today 界面点时间轴片段。选项是 Focused、Normal、Distracted、Rest、Not sure。反馈可修订和撤回，原预测保留。一个 15 分钟反馈不会算作几十个独立样本。

个人分类器至少需要 30 个独立工作状态片段，跨 2 天、3 个工作段，且每一类至少 5 个片段。达到门槛后才尝试训练，并不代表效果可靠。训练候选会与先验及当前模型比较；还需后续独立审计反馈支持，才启用。界面提供手动训练、取消、版本状态和回退。数据不足时继续使用先验。

“此刻是否想休息”是单独的自愿 Yes / No / Not sure 反馈。它用于校准负荷参数，不能由专注标签、关闭提醒或未回复推导。至少 20 次独立自报、跨 3 天、Yes / No 各 5 次；恢复参数还要求 5 个有前后反馈的休息段。搜索比较默认值、Random Search 和 TPE，各 64 次候选计算。候选仍需后续验证，历史数据不会被静默改写。

自动标签请求每天最多 2 次、间隔至少 90 分钟；所有自动浮层合计每天 6 次、每小时 2 次。休息提示冷却至少 30 分钟。支持延后 5 / 15 / 30 分钟、安静时段和完全关闭提醒。无法可靠读取系统免打扰或全屏状态，请使用应用内控制。

## 数据管理与复现

真实、演示和测试模式使用独立数据库和模型目录。默认无遥测、无上传。数据本地保存，但未实现应用级加密。设置提供导出字段预览、范围删除和全部删除；删除会处理相关特征、反馈和模型依赖。默认原始统计桶保留 7 天，特征和派生历史保留 90 天。

```sh
.venv/bin/focuspet scenarios
.venv/bin/focuspet replay reading --output experiments/reading-replay.json
.venv/bin/focuspet evaluate --config experiments/config.json
.venv/bin/focuspet train --data experiments/results/seed-7/work-data.json --mode synthetic-demo --output .runtime/synthetic-models
.venv/bin/focuspet calibrate --data experiments/results/seed-7/load-data.json --mode synthetic-demo --output .runtime/synthetic-parameters
.venv/bin/focuspet export-demo --scenario workday --output demo
```

`demo/index.html` 可直接在浏览器打开，无需服务器。它仅播放 Python 核心导出的合成轨迹，无法读取访问者的桌面和全局键鼠。

实际已测状态请查看 [平台状态](platform-status.md)、[原生测试清单](native-testing.md)、[已知限制](known-limitations.md) 和 [模拟结果](../experiments/results/report.md)。模拟结果不代表真实用户长期改善。
