#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ $(id -u) == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo 'This bundle requires x86_64.' >&2; exit 1; }
kernel=$(uname -r | cut -d- -f1)
[[ $(printf '%s\n' 4.18 "$kernel" | sort -V | head -1) == 4.18 ]] || {
  echo 'This installer requires Linux kernel 4.18 or newer.' >&2; exit 1;
}
for tool in systemctl tar sha256sum; do command -v "$tool" >/dev/null; done
sha256sum -c SHA256SUMS >/dev/null
target=/opt/jht-docker
unit=/etc/systemd/system/jht-docker.service
if [[ -e $target || -e $unit || -e /usr/local/bin/jht-docker ]]; then
  echo 'Dedicated Docker installation already exists. Run jht-docker info; do not overwrite it.' >&2
  exit 1
fi
install -d -m 0755 "$target/bin"
tar -xzf engine/docker-28.5.2.tgz --strip-components=1 -C "$target/bin"
printf '{}\n' > "$target/daemon.json"
cat > "$unit" <<'EOF'
[Unit]
Description=JianHuiTong dedicated offline Docker engine
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
Environment=PATH=/opt/jht-docker/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStart=/opt/jht-docker/bin/dockerd --config-file=/opt/jht-docker/daemon.json --host=unix:///run/jht-docker.sock --data-root=/var/lib/jht-docker --exec-root=/run/jht-docker-exec --pidfile=/run/jht-docker.pid --bridge=none --iptables=false --ip6tables=false --ip-forward=false --ip-masq=false --userland-proxy=false --storage-driver=overlay2
ExecReload=/bin/kill -s HUP $MAINPID
Restart=on-failure
RestartSec=5
TimeoutStartSec=120
LimitNOFILE=infinity
LimitNPROC=infinity
TasksMax=infinity
Delegate=yes
KillMode=process

[Install]
WantedBy=multi-user.target
EOF
cat > /usr/local/bin/jht-docker <<'EOF'
#!/bin/sh
exec /opt/jht-docker/bin/docker --host unix:///run/jht-docker.sock "$@"
EOF
chmod 0755 /usr/local/bin/jht-docker
command -v modprobe >/dev/null && modprobe overlay || true
systemctl daemon-reload
systemctl enable --now jht-docker.service
jht-docker version
jht-docker info --format 'Storage={{.Driver}} Server={{.ServerVersion}}'
echo 'Dedicated engine ready. Existing Podman and host firewall rules were not replaced.'
