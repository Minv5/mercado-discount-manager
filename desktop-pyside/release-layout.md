# 正式发布目录说明

正式发布物是完整目录，不是单个 EXE。

```text
dist-pyside/
  美客多活动助手/
    美客多活动助手.exe
    _internal/
      node/node.exe
      app/src/server.js
      app/public/
      PySide6/
      shiboken6/
      assets/
      Python 与 Qt 运行依赖
```

安装时必须整体复制 `美客多活动助手` 目录。单独复制主 EXE 无法运行。

旧 WinForms 安装版继续保留在原目录，作为回退版本；正式安装目录保持兼容。安装器只维护当前用户开始菜单中的“美客多活动助手”快捷方式，不创建或删除桌面快捷方式。
