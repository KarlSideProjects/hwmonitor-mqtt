# Python 開發與驗證

[返回首頁](../../README.md) · [文件中心](../README.md)

本專案有 `pyproject.toml`，建議使用 `uv`：

```bash
uv sync --locked
make check
```

`make check` 會執行完整 pytest 回歸測試。支援 Python 3.11 與 3.13，PowerShell 測試需 Linux 上的 `pwsh`。

原生 PVE 安裝整合檢查以 Python 3.13 執行，只應在 GitHub-hosted CI 或可丟棄的 Linux VM 執行；它會暫時建立 `/opt` 專案與唯一的 systemd unit，使用 `umask 077`、0600 空白設定與 loopback MQTT broker，結束時移除測試服務及檔案。若 systemd 無法確認服務已停止，檢查會失敗並保留檔案，輸出中會列出需要清理的路徑。請勿在正式主機執行：

```bash
make native-install-smoke
```
