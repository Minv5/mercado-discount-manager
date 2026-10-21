[English](README.md) | 简体中文

# 美客多活动管家 (Mercado Libre Discount Manager)

[![Release](https://img.shields.io/github/v/release/Minv5/mercado-discount-manager?color=brightgreen&label=Release)](https://github.com/Minv5/mercado-discount-manager/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PySide6-green.svg)](https://wiki.qt.io/Qt_for_Python)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

基于 **Python + PySide6** 原生架构开发的美客多（Mercado Libre）跨境多店铺促销活动管理与运营自动化桌面工作台，原生支持 **Windows (x64)** 与 **macOS (Apple Silicon)** 双平台。

软件全面覆盖活动申报、净回款算价、实时汇率订单履约倒计时、客服多语言即时沟通与售后纠纷风控，执行**所有写操作前强制二次弹窗确认**，严密守护跨境店铺资金与运营安全。

---

## 一、Windows / macOS 用户如何下载使用？

### 方式 1：直接下载免安装成品包（推荐，无需配置任何环境）

每次版本更新后，GitHub 云端会自动编译打包 Windows 与 macOS 安装包并自动发布至 **[最新发行版发布区（Latest Releases）](https://github.com/Minv5/mercado-discount-manager/releases/latest)**：

1. **Windows 系统用户**：
   * 点击前往 **[最新发行版发布区（Latest Releases）](https://github.com/Minv5/mercado-discount-manager/releases/latest)**；
   * 下载最新版本的 **`美客多活动管家-Windows-x64-v*.zip`**；
   * 解压压缩包后，直接双击文件夹内的 **`美客多活动管家.exe`** 即可打开使用（无需安装 Python 环境）。
2. **macOS 系统用户**：
   * 点击前往 **[最新发行版发布区（Latest Releases）](https://github.com/Minv5/mercado-discount-manager/releases/latest)**；
   * 下载最新版本的 **`美客多活动管家-macOS-arm64-v*.zip`**；
   * 解压后将 **`美客多活动管家.app`** 拖入 `/Applications`（应用程序目录）即可直接打开使用。

---

### 方式 2：从源码运行或本地自行打包

如果您克隆或下载了本仓库源码，可按以下方式直接运行或打包：

#### Windows 系统（需已安装 [Python 3.10+](https://www.python.org/downloads/)）
* **一键启动软件**：直接双击仓库根目录下的 **`run-windows.bat`**（首次运行会自动创建 `.venv` 虚拟环境并安装依赖，随后自动打开主界面）。
* **一键打包 `.exe`**：直接双击仓库根目录下的 **`build-windows.bat`**（或执行 `python scripts/build-windows.py`），打包产物位于 `dist-win\美客多活动管家\美客多活动管家.exe`。

#### macOS 系统（需已安装 Python 3.10+）
* **直接启动软件**：
  ```bash
  python3 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt
  ./.venv/bin/python desktop-pyside/app.py
  ```
* **一键打包 `.app` 并更新至桌面与应用程序目录**：
  ```bash
  ./scripts/build-macos.sh
  ```

---

## 二、核心功能与架构说明

```text
mercado-discount-manager/
├── desktop-pyside/                  # 正式桌面客户端与原生 Python 引擎目录
│   ├── app.py                       # 程序主入口（支持离线冒烟健康检查）
│   ├── main_window.py               # PySide6 主工作台与模块布局
│   ├── dialogs.py                   # 店铺授权、活动设置与详情对话框
│   ├── orders/                      # 订单履约模块（发货SLA倒计时、实时外汇换算、多页面单批量打印）
│   ├── service_center/              # 客户服务中心（买家即时消息、多语言智能翻译、售后纠纷风控）
│   ├── ui_common/                   # 通用UI组件库、多规格图片缓存池与全屏灯箱预览
│   └── engine/                      # 原生核心业务引擎
│       ├── auth.py                  # OAuth 2.0 令牌自动刷新、续期与隔离存储
│       ├── client.py                # HTTPS Keep-Alive 长连接池与高并发 API 客户端
│       ├── pricing.py               # 净回款（net_proceeds）权威算价引擎
│       ├── exchange_rate.py         # 实时联网外汇牌价同步引擎（带本地二级缓存）
│       ├── executor.py              # 多站点促销活动并发报名与取消执行器
│       ├── item_cleaner.py          # 刊登表现评分、全周期流量分析与风险商品清理引擎
│       ├── webhook_worker.py        # 实时 Webhook 回调监听与事件分流
│       ├── bridge.py                # UI 与引擎通信桥接层
│       └── crypto.py                # 本地凭据 AES-GCM 高度加密安全模块
├── scripts/                         # 自动化维护与跨平台打包脚本
├── run-windows.bat                  # Windows 源码一键启动脚本
├── build-windows.bat                # Windows 本地一键打包脚本
└── requirements.txt                 # Python 依赖清单
```

### 本地数据存储与安全准则
* **Windows 数据目录**：`%LOCALAPPDATA%\MercadoDiscountManagerStandalone\data`
* **macOS 数据目录**：`~/Library/Application Support/MercadoDiscountManagerStandalone/data`
* **敏感凭据安全**：所有店铺授权凭据均经由本地独立的 `AES-GCM` 算法加密后安全保存在本机数据目录中，绝对严禁写入代码仓库或外发。
