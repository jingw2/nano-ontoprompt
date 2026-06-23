# 信创内网服务器离线升级 OpenSSH 操作步骤

本文记录在无外网权限的信创服务器上，通过离线包源码编译升级 OpenSSH 的标准流程。

适用环境：

- 服务器无外网权限
- 已准备 OpenSSH 源码包和离线 RPM 依赖包
- 系统 OpenSSL 为发行版自带版本，例如 `OpenSSL 1.1.1k FIPS`
- SSH 服务端口为自定义端口，例如 `22333`

> 重要：优先不要升级系统 OpenSSL。信创环境中的 OpenSSL FIPS 通常与系统合规、安全基线和系统库绑定较深。OpenSSH 可以继续链接系统 OpenSSL。

## 1. 升级前检查和备份

查看当前版本、端口和登录策略：

```bash
ssh -V
sshd -V 2>&1 | head -1
grep -E '^Port|^PermitRootLogin|^PasswordAuthentication' /etc/ssh/sshd_config
```

备份关键文件：

```bash
cp -a /etc/ssh /etc/ssh.bak.$(date +%F)
cp -a /etc/pam.d/sshd /etc/pam.d/sshd.bak.$(date +%F)
cp -a /usr/sbin/sshd /usr/sbin/sshd.bak
cp -a /usr/bin/ssh /usr/bin/ssh.bak
cp -a /lib/systemd/system/sshd.service /lib/systemd/system/sshd.service.bak
```

升级期间至少保留一个已登录 SSH 会话不关闭，最好额外打开一个备用会话。若服务器有控制台、IPMI、KVM 或云厂商带外控制台，也应提前确认可用。

## 2. 安装离线编译依赖

进入离线 RPM 包目录：

```bash
cd /home/Tools/update/rpms
rpm -Uvh *.rpm --force --nodeps
```

验证基础依赖：

```bash
gcc --version
make --version
rpm -qa | grep -E 'openssl-devel|pam-devel|zlib-devel|krb5-devel|make-devel' | sort
```

至少应具备：

- `gcc`
- `make`
- `openssl-devel`
- `pam-devel`
- `zlib-devel`
- `krb5-devel`

## 3. 编译安装 OpenSSH

进入源码目录：

```bash
cd /home/Tools/update
tar xf openssh-10.3p1.tar.gz
cd openssh-10.3p1
```

配置编译参数：

```bash
./configure \
  --prefix=/usr \
  --sysconfdir=/etc/ssh \
  --with-pam \
  --with-selinux \
  --with-md5-passwords
```

编译并安装：

```bash
make -j$(nproc)
make install
```

安装完成后检查：

```bash
ssh -V
sshd -V 2>&1 | head -1
grep ^Port /etc/ssh/sshd_config
sshd -t && echo "sshd_config OK"
```

期望结果类似：

```text
OpenSSH_10.3p1, OpenSSL 1.1.1k FIPS
Port 22333
sshd_config OK
```

## 4. 处理 systemd 的 CRYPTO_POLICY

源码编译版 OpenSSH 可能不兼容发行版通过 systemd 注入的 `$CRYPTO_POLICY`。如果不处理，可能出现：

```text
SSH protocol handshake error
Connection reset by peer
```

先检查 systemd 启动命令和系统加密策略：

```bash
grep ExecStart /lib/systemd/system/sshd.service
cat /etc/sysconfig/sshd 2>/dev/null
cat /etc/crypto-policies/back-ends/opensshserver.config 2>/dev/null
```

如果看到类似：

```ini
ExecStart=/usr/sbin/sshd -D $OPTIONS $CRYPTO_POLICY $PERMITROOTLOGIN
```

建议修改为：

```ini
ExecStart=/usr/sbin/sshd -D $OPTIONS $PERMITROOTLOGIN
```

编辑文件：

```bash
vi /lib/systemd/system/sshd.service
```

保存后重新加载 systemd：

```bash
systemctl daemon-reload
grep ExecStart /lib/systemd/system/sshd.service
```

确认 `ExecStart` 中已经没有 `$CRYPTO_POLICY`。

说明：

- `/etc/sysconfig/sshd` 是 sshd 服务的启动变量配置。
- `/etc/crypto-policies/back-ends/opensshserver.config` 是发行版系统级 SSH 加密策略。
- 发行版 RPM 版 OpenSSH 通常能适配这套策略；源码编译版 OpenSSH 未必兼容。
- 去掉 `$CRYPTO_POLICY` 后，sshd 主要按 `/etc/ssh/sshd_config` 启动。

## 5. 可选：注释新版废弃配置项

如果 `sshd -t` 提示以下旧配置项不支持或已废弃：

- `GSSAPIAuthentication`
- `GSSAPICleanupCredentials`
- `RSAAuthentication`
- `RhostsRSAAuthentication`

可注释掉：

```bash
sed -i.bak \
  -e 's/^GSSAPIAuthentication/#GSSAPIAuthentication/' \
  -e 's/^GSSAPICleanupCredentials/#GSSAPICleanupCredentials/' \
  -e 's/^RSAAuthentication/#RSAAuthentication/' \
  -e 's/^RhostsRSAAuthentication/#RhostsRSAAuthentication/' \
  /etc/ssh/sshd_config

sshd -t && echo "sshd_config OK"
```

## 6. 重启 sshd 生效

重启前确认至少还有一个 SSH 会话在线：

```bash
systemctl restart sshd
systemctl status sshd -l
ss -tlnp | grep 22333
```

新开终端窗口验证登录：

```bash
ssh -p 22333 root@172.31.10.56
```

登录后确认版本：

```bash
ssh -V
sshd -V 2>&1 | head -1
```

## 7. 回滚方案

如果重启后无法登录，使用仍在线的旧 SSH 会话或带外控制台执行：

```bash
cp -f /usr/sbin/sshd.bak /usr/sbin/sshd
cp -f /usr/bin/ssh.bak /usr/bin/ssh
cp -f /etc/pam.d/sshd.bak.* /etc/pam.d/sshd 2>/dev/null
cp -f /lib/systemd/system/sshd.service.bak /lib/systemd/system/sshd.service

systemctl daemon-reload
sshd -t
systemctl restart sshd
```

检查服务：

```bash
systemctl status sshd -l
ss -tlnp | grep 22333
```

必要时查看日志：

```bash
journalctl -u sshd -n 80 --no-pager
tail -50 /var/log/secure
```

## 8. 升级经验总结

- 源码升级 OpenSSH 时，不建议同时升级系统 OpenSSL。
- `sshd -t` 只检查 `/etc/ssh/sshd_config`，不会检查 systemd 的 `$CRYPTO_POLICY`。
- 信创/RHEL 系系统中的 `$CRYPTO_POLICY` 可能导致源码版 OpenSSH 握手失败。
- 重启 sshd 前应检查并必要时移除 `sshd.service` 中的 `$CRYPTO_POLICY`。
- 重启前必须保留备用 SSH 会话或控制台入口。
- 若有发行版厂商提供的 OpenSSH RPM，优先使用厂商 RPM，通常比源码编译更稳。
