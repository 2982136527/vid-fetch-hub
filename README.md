# Vid-Fetch-Hub

[![Docker Build](https://github.com/qiuhu/vid-fetch-hub/actions/workflows/docker-build.yml/badge.svg)](https://github.com/qiuhu/vid-fetch-hub/pkgs/container/vid-fetch-hub)

通用视频站元数据爬取 + 实时代理转发工具。

## 功能

- 爬取多个视频站的元数据（标题、标签、简介、分类）
- 生成 Emby/Jellyfin 兼容的目录结构（STRM + NFO + 封面）
- 内置 HTTP 代理服务，实时获取新鲜播放链接
- 全自动守护模式：全量回填 → 定时增量更新
- Docker 部署，NAS 友好

## 架构

```
爬虫 → 元数据 + 封面 → Emby 目录结构
                          ↓
代理服务器 :8383 ← Emby/Jellyfin 播放请求
       ↓
实时抓取源站播放链接 → 重定向/代理视频流
```

## 快速开始

```bash
# 修改配置
vim config.yaml

# 手动模式
python3 main.py

# 全自动模式
python3 main.py --auto
```

## Docker

```bash
# 导入镜像
docker load -i vid-fetch-hub-x86.tar.gz
```

### 镜像来源

**方式 A：从 GitHub Container Registry 拉取（推荐）**

```bash
# 拉取 latest
docker pull ghcr.io/qiuhu/vid-fetch-hub:latest

# 拉取指定版本
docker pull ghcr.io/qiuhu/vid-fetch-hub:0.0.1
```

**方式 B：导入本地 tar 包**

```bash
docker load -i vid-fetch-hub-x86.tar.gz
```

### 首次部署

先提取默认配置，改好后再启动，这样生成的 STRM 直接就是你的 IP，不用重新爬。

```bash
# 1. 创建目录
mkdir -p /path/to/config /path/to/output

# 2. 从镜像里提取默认配置模板
docker run --rm vid-fetch-hub:x86 cat /app/config/config.yaml > /path/to/config/config.yaml

# 3. 编辑配置（改完再启动）
vim /path/to/config/config.yaml
# 必改项：public_url 改成你的 NAS 地址
# 可选项：http_proxy、开关站点等

# 4. 启动
docker run -d \
  --name vid-fetch-hub \
  --restart unless-stopped \
  -p 8383:8383 \
  -v /path/to/config:/config \
  -v /path/to/output:/output \
  -e VFH_HTTP_PROXY=http://your-proxy:1080 \
  vid-fetch-hub:x86
```

**方式二：先启后配**

容器启动时自动检测 `/config/config.yaml`，不存在则从镜像复制默认配置，然后你停掉改好再重启。

```bash
# 1. 创建目录，启动
mkdir -p /path/to/config /path/to/output
docker run -d --name vid-fetch-hub \
  --restart unless-stopped -p 8383:8383 \
  -v /path/to/config:/config -v /path/to/output:/output \
  -e VFH_PUBLIC_URL=http://your-nas-ip:8383 \
  vid-fetch-hub:x86

# 2. 停掉，编辑配置
docker stop vid-fetch-hub
vim /path/to/config/config.yaml

# 3. 重启
docker start vid-fetch-hub

# 查看日志
docker logs -f vid-fetch-hub
```

### 环境变量说明
| `VFH_PUBLIC_URL` | STRM 文件中的公网地址 |
| `VFH_HTTP_PROXY` | 出口 HTTP 代理 |
| `VFH_HTTPS_PROXY` | 出口 HTTPS 代理 |

## 免责声明

本工具仅用于技术研究和学习。用户应遵守目标网站的使用条款和当地法律法规。
