# Mercado Discount Manager (美客多活动管家)

English | [简体中文](README_zh.md)

[![Release](https://img.shields.io/github/v/release/Minv5/mercado-discount-manager?color=brightgreen&label=Release)](https://github.com/Minv5/mercado-discount-manager/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![GUI](https://img.shields.io/badge/GUI-PySide6-green.svg)](https://wiki.qt.io/Qt_for_Python)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

**Mercado Discount Manager** is a localized, production-grade desktop application (built with **Python & PySide6**) designed for cross-border Mercado Libre sellers. It streamlines promotional campaign management, real-time FX-linked pricing, automated order fulfillment countdowns, item cleanup, and post-sale claim reputation assessment—enforcing **explicit two-phase confirmation before any live API write operations** to prevent costly merchant errors.

---

## 📥 Download Pre-built Binaries (Recommended)

No Python environment or local configuration required. Automated standalone releases are available for Windows and macOS:

* **Windows**: Download `美客多活动管家-Windows-x64-v*.zip` from **[Latest Releases](https://github.com/Minv5/mercado-discount-manager/releases/latest)**, unzip, and launch `美客多活动管家.exe`.
* **macOS**: Download `美客多活动管家-macOS-arm64-v*.zip` from **[Latest Releases](https://github.com/Minv5/mercado-discount-manager/releases/latest)**, unzip, and drag `美客多活动管家.app` into `/Applications`.

---

## ✨ Key Capabilities

* **Two-Phase Safe Write Confirmation:** Protects live storefronts by requiring explicit verification before executing bulk promotional updates, discount cancellations, or listing modifications.
* **Order Fulfillment & Live FX Rate Sync:** Dynamically links real-time dispatch countdown timers with live onshore exchange rate conversions and batch shipping label printing.
* **Customer Service & Claims Assessment:** Real-time post-sale message management, multi-language instant translation, and buyer claim risk assessment.
* **Two-Phase Item Cleaner Engine:** Automated identification, listing performance evaluation, and safe batch cleanup of underperforming listings.
* **Multi-Store OAuth2 & Webhook Architecture:** Decoupled callback handling, multi-store App whitelisting, and local AES-GCM credential encryption.

---

## 📂 Project Structure

```text
mercado-discount-manager/
├── desktop-pyside/          # PySide6 desktop client & native Python engines
│   ├── app.py               # Main application entry point
│   ├── main_window.py       # Main GUI workspace window
│   ├── dialogs.py           # Store management & status query dialogs
│   ├── orders/              # Order fulfillment, dispatch SLA timers & shipping labels
│   ├── service_center/      # Customer service messages, translations & claim analysis
│   ├── ui_common/           # Reusable UI widgets, image cache & lightbox preview
│   └── engine/              # Core business engines (pricing, auth, cleaner, executor)
├── scripts/                 # Automated build and packaging scripts (macOS & Windows)
├── requirements.txt         # Python dependencies
├── run-windows.bat          # Quick launcher for Windows environments
├── build-windows.bat        # Packaging script for Windows
└── SECURITY.md              # Security policy and vulnerability reporting
```

---

## 🚀 Quick Start (From Source)

### Prerequisites
* **OS:** Windows 10/11 or macOS 12+
* **Runtime:** Python 3.10+

### Windows
```cmd
# 1. Clone repository
git clone https://github.com/Minv5/mercado-discount-manager.git
cd mercado-discount-manager

# 2. Run directly (automatically initializes venv & installs dependencies)
run-windows.bat

# 3. Build standalone executable (Optional)
build-windows.bat
```

### macOS
```bash
# 1. Clone repository
git clone https://github.com/Minv5/mercado-discount-manager.git
cd mercado-discount-manager

# 2. Setup environment & launch
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python desktop-pyside/app.py

# 3. Build standalone bundle (Optional)
./scripts/build-macos.sh
```

---

## 🛡️ Security & Governance

This repository follows strict open-source governance and security practices:
* **Explicit Write Guardrails:** No silent background mutation of live marketplace prices or promotions.
* **Credential Isolation:** OAuth tokens and sensitive credentials are encrypted locally with AES-GCM and never committed.
* Please refer to [`SECURITY.md`](SECURITY.md), [`CONTRIBUTING.md`](CONTRIBUTING.md), and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for detailed policies.

---

## 📄 License

Licensed under the **Apache-2.0 License**. See [`LICENSE`](LICENSE) for full details.
