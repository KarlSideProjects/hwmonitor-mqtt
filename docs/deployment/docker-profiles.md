# Docker Profiles

## Profile 一覽

- `agent`: 系統監控發送端
- `esp-agent`: 在被監控主機執行，為 ESP 等接收端提供精簡 payload 與低頻率發送；不是安裝到 ESP 的韌體
- `broker`: 內建 Mosquitto
- `web`: Web 監控介面
- `full`: 一次啟動完整組合

## 常用命令

```bash
# 查看 profile
docker compose config --profiles

# 啟動完整模式
docker compose --profile full up -d

# 看即時日誌
docker compose logs -f
```
