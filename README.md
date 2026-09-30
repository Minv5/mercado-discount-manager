# 美客多活动管家 (Mercado Libre Discount Manager)

基于 **Python + PySide6** 原生架构开发的美客多（Mercado Libre）跨境多店铺、多站点促销活动自动化管理桌面软件，支持 **Windows (x64)** 与 **macOS (Apple Silicon)** 双平台运行。

---

## 一、Windows / macOS 用户如何下载使用？

### 方式 1：直接下载免安装成品包（推荐，无需配置任何环境）

每次版本更新后，GitHub 云端会自动编译打包 Windows 与 macOS 安装包并发布至仓库右侧的 **[Releases（发行版下载区）](../../releases)**：

1. **Windows 系统用户**：
   * 点击仓库页面右侧的 **[Releases](../../releases)**；
   * 下载最新版本的 **`美客多活动管家-Windows-x64-v2.0.35.zip`**；
   * 解压压缩包后，直接双击文件夹内的 **`美客多活动管家.exe`** 即可打开使用（无需安装 Python）。
2. **macOS 系统用户**：
   * 点击仓库页面右侧的 **[Releases](../../releases)**；
   * 下载最新版本的 **`美客多活动管家-macOS-arm64-v2.0.35.zip`**；
   * 解压后将 **`美客多活动管家.app`** 拖入 `/Applications`（应用程序）即可打开使用。

---

### 方式 2：从源码运行或本地自行打包

如果您克隆或下载了本仓库源码，可按以下方式直接运行或打包：

#### Windows 系统（需已安装 [Python 3.11+](https://www.python.org/downloads/)）
* **一键启动软件**：直接双击仓库根目录下的 **`run-windows.bat`**（首次运行会自动创建 `.venv` 虚拟环境并安装依赖，随后自动打开主界面）。
* **一键打包 `.exe`**：直接双击仓库根目录下的 **`build-windows.bat`**（或执行 `python scripts/build-windows.py`），打包产物位于 `dist-win\美客多活动管家\美客多活动管家.exe`。

#### macOS 系统（需已安装 Python 3.11+）
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
├── desktop-pyside/                  # 正式桌面客户端与原生引擎目录
│   ├── app.py                       # 程序启动入口（含离线冒烟自检支持）
│   ├── main_window.py               # PySide6 主窗口与交互界面
│   ├── dialogs.py                   # 授权管理、商品状态查询与对话框组件
│   ├── engine/                      # 原生 Python 核心业务引擎
│   │   ├── auth.py                  # OAuth 2.0 令牌自动刷新与持久化
│   │   ├── client.py                # HTTPS Keep-Alive 连接池与高并发 API 客户端
│   │   ├── pricing.py               # 净回款（net_proceeds）权威算价引擎
│   │   ├── executor.py              # 多站点促销活动并发报名与取消执行器
│   │   ├── webhook_worker.py        # 实时 Webhook 回调监听与双定价体系自动分流
│   │   ├── bridge.py                # UI 与本地引擎通信桥接层
│   │   └── crypto.py                # AES-GCM 本地凭据加密存储
│   └── tests/                       # 单元测试套件
├── scripts/
│   ├── build-macos.py               # macOS 打包脚本
│   ├── build-macos.sh               # macOS 一键构建入口
│   └── build-windows.py             # Windows 打包脚本
├── run-windows.bat                  # Windows 源码一键运行脚本
├── build-windows.bat                # Windows 本地一键打包脚本
└── requirements.txt                 # Python 依赖清单
```

### 本地数据存储位置
* **Windows**：`%LOCALAPPDATA%\MercadoDiscountManagerStandalone\data`
* **macOS**：`~/Library/Application Support/MercadoDiscountManagerStandalone/data`
* 所有店铺 `OAuth Token` 均通过本地 `AES-GCM` 密钥加密存储于上述数据目录中，不会上传或写入代码仓库。
