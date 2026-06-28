# M5 NAS / SMB / NFS 部署说明

## 1. 基本原则

MediaAI Renamer 只访问后端服务进程能看到的路径。浏览器所在电脑能访问某个目录，并不代表后端服务也能访问。

M5 不自动执行系统挂载命令：

- 不执行 `net use`。
- 不执行 `mount`。
- 不保存 NFS 密码、Kerberos keytab、principal 或 realm。
- WebDAV、FTP、SFTP、S3 暂不支持真实扫描和重命名。

## 2. Windows UNC / SMB

UNC 路径示例：

```text
\\NAS\Movies
\\192.168.1.10\media\movie
```

使用要求：

- 后端服务进程所在 Windows 主机必须能访问该共享。
- 如需账号密码访问，应先确保系统层或服务运行用户具备共享访问权限。
- 媒体源中 SMB 密码加密保存，不在列表、接口响应和普通日志中显示明文。

连接失败时建议检查：

- UNC 路径是否以 `\\` 开头。
- NAS 或 Windows 共享是否在线。
- 当前运行后端服务的用户是否有共享读写权限。
- Windows 防火墙和 SMB 服务是否允许访问。

## 3. Linux / NAS / fnOS 已挂载路径

路径示例：

```text
/mnt/media
/mnt/nfs/media
/volume1/video
/share/Movies
```

使用要求：

- 先在 Linux、NAS 或 fnOS 系统中完成挂载。
- 媒体源路径填写后端服务实际可访问的路径。
- NFS 依赖系统 UID/GID 权限模型，系统不提供用户名和密码输入框。

## 4. NFS 客户端安装

Ubuntu / Debian：

```bash
sudo apt update
sudo apt install -y nfs-common
```

CentOS / RHEL：

```bash
sudo yum install -y nfs-utils
```

Alpine：

```bash
sudo apk add nfs-utils
```

## 5. NFS 挂载示例

临时挂载：

```bash
sudo mkdir -p /mnt/nfs/media
sudo mount -t nfs -o vers=4 192.168.1.10:/volume1/media /mnt/nfs/media
```

永久挂载 `/etc/fstab` 示例：

```text
192.168.1.10:/volume1/media /mnt/nfs/media nfs defaults,_netdev,vers=4 0 0
```

挂载后检查：

```bash
mount | grep nfs
ls -la /mnt/nfs/media
```

## 6. Docker 路径映射

推荐方式是宿主机先挂载 NFS，再映射到容器：

```yaml
services:
  mediaai-renamer:
    volumes:
      - /mnt/nfs/media:/app/media
```

此时媒体源路径应填写容器内路径：

```text
/app/media
```

不是宿主机路径：

```text
/mnt/nfs/media
```

Docker volume driver 也可以挂载 NFS，但不同环境差异较大，M5 文档仅作为参考，正式部署前应先用宿主机挂载加 bind mount 验证。

## 7. UID / GID 权限

如果容器内可以看到目录但无法读写，通常是 UID / GID 不匹配。

检查宿主机文件权限：

```bash
ls -ln /mnt/nfs/media
```

检查容器内运行用户：

```bash
id
```

处理建议：

- 让容器运行用户拥有目标目录读写权限。
- 在 NAS 侧给对应用户或匿名映射配置读写权限。
- 避免只给浏览器客户端用户授权，而忽略后端服务运行用户。

## 8. 常见问题

路径不可见：

- 检查路径是否写给后端服务所在机器或容器。
- Docker 场景检查 volume 映射是否存在。

权限不足：

- 检查运行后端服务的用户。
- 检查 NAS 共享权限、NFS 导出权限和 UID/GID 映射。

共享断开：

- 检查网络、NAS 电源、休眠策略和防火墙。
- NFS 场景检查挂载是否仍存在。

NFS 文件句柄过期：

- 可能是 NAS 端目录重新导出、重启或挂载状态变化。
- 建议重新挂载后再测试连接。
