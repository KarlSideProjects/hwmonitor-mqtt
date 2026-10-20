# 硬體文件中心

本區整理採集端的 GPU 能力需求與顯示端的設備設定。採集主機與顯示設備可以分開部署；本專案不限定使用某一種掌機或螢幕。

## 結構

```text
docs/hardware/
├─ gpu/
│  └─ requirements.md
├─ display/
│  └─ raspberrypi-3.5inch.md
```

## 文件入口

- GPU 需求與能力矩陣：[`gpu/requirements.md`](./gpu/requirements.md)
- Raspberry Pi 3.5 吋顯示器設定：[`display/raspberrypi-3.5inch.md`](./display/raspberrypi-3.5inch.md)

## 關聯建議

1. 若你是要讓 Viewer 正確顯示 GPU 資訊：先讀 GPU 文件，再讀顯示器文件。
2. 若你是要在小螢幕部署（Pi 3.5 吋）：先讀顯示器文件，再回頭確認 GPU 工具需求。

## 外部顯示端整合

同屬 KarlSideProjects 的 [mqtt-handheld-hardware-monitor](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor) 提供 Linux 掌機顯示端，目前有 R36 MAX 2 與 TrimUI Smart Pro S 的安裝腳本及[實機按鍵與進入面板圖解](https://github.com/KarlSideProjects/mqtt-handheld-hardware-monitor/blob/main/docs/device-controls.md)。

一般網頁、TUI、Pygame 或自訂 MQTT 接收程式，也可以作為顯示端。掌機安裝腳本適用的設備與限制，請以該專案的硬體清單及安裝說明為準。
