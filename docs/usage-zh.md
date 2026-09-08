# Focus Pet 中文使用说明

Focus Pet 是一个像素桌面伙伴，根据本机活动统计估计工作状态、显示工作节奏负荷，并提供休息提醒。状态估计不代表实际工作效率，负荷指数也不是医学疲劳测量。

## 安装与启动

支持 macOS 14+ 和 Apple Silicon。Windows、Linux 暂无全局活动采集器。从 [发布页](https://github.com/yeopbong/focuspet/releases/tag/v0.1.0) 下载 macOS ZIP 和 `SHA256SUMS.txt`，核对 SHA256 后解压，将 `Focus Pet.app` 移入 Applications。包内已包含 Python。当前包只有临时签名，没有 Developer ID 签名或公证；若被系统阻止，参考 [Apple 的逐应用放行说明](https://support.apple.com/en-us/102445)，或采用源码安装。

源码安装使用 Python 3.11，在项目根目录执行：

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-macos-arm64.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/focuspet doctor
.venv/bin/focuspet run
```

首次启动选择角色和工作画像，然后决定是否授权统计采集。画像是可随时更改的初始先验。macOS 输入监听还需要系统设置中的额外授权。拒绝采集后仍可使用桌面伙伴，运行 `focuspet demo` 可打开无需输入权限的演示。

## 日常操作

- 单击角色：短暂回应；拖动可调整位置。
- 双击：查看状态、Focus、Work Load 和观测依据，纠正最近 1 / 5 / 15 分钟的状态。
- 右键：休息、延后提醒、安静模式、选择角色、设置、隐藏和退出。
- 菜单栏托盘：打开状态、恢复角色、暂停/继续采集、关闭完全点击穿透、退出。
- Today 时间轴：查看历史片段，提交、修订或撤回反馈。

低输入阅读或浏览器活动无法可靠分辨时可能显示 Unknown。休息、暂停或缺失时 Focus 显示“—”。Work Load 的参考线为 100，负数表示恢复缓冲。休息会逐渐降低负荷；跨日、锁屏、无输入和离线不会自动清零。重启后可继续旧周期、确认限定时长的休息，或主动开始新周期。

个人分类器需要足够的自愿纠正和后续独立反馈才会启用。单独的“此刻是否想休息”反馈用于校准负荷参数。设置提供训练、取消、版本状态和回退；数据不足时继续使用规则先验。具体条件见 [模型与参数](models-and-parameters.md)。

可延后提醒 5 / 15 / 30 分钟，设置安静时段或关闭提醒。应用无法可靠读取系统免打扰和全屏状态，请使用应用内控制。

## 数据与演示

数据保存在本机，无账号、遥测或上传；本地文件未加密。真实、演示和测试模式使用独立目录。设置可预览导出字段、删除指定范围或全部数据。默认活动统计保留 7 天，特征和派生历史保留 90 天。详见 [隐私与数据控制](privacy.md)。

[在线演示](https://yeopbong.github.io/focuspet/) 播放 Python 核心导出的合成轨迹，无法读取访问者的桌面。需要离线演示时，可下载发布页的演示包；如浏览器限制直接打开本地文件，可在演示目录运行 `python3 -m http.server 8000 --bind 127.0.0.1`，再访问 `http://127.0.0.1:8000`。

[导出历史回放](export-replay.md) · [模拟实验运行方法](experiment-protocol.md) · [模拟结果](../experiments/results/report.md)
