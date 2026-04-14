#!/usr/bin/env bash
# installs Jenkins LTS as a native systemd service (not Docker).
# run once: bash scripts/install_jenkins_systemd.sh
# requires: sudo, Ubuntu/Debian with apt.

set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run with sudo, e.g.: sudo bash $0" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

KEYRING="/etc/apt/keyrings/jenkins-keyring.asc"
LIST="/etc/apt/sources.list.d/jenkins.list"

echo "==> installing java 21 (jenkins requires java before the jenkins package)"
apt-get update -qq
apt-get install -y ca-certificates fontconfig wget
apt-get install -y openjdk-21-jre-headless

java -version

echo "==> adding jenkins apt repository (LTS)"
install -d -m 0755 /etc/apt/keyrings
wget -q -O "$KEYRING" https://pkg.jenkins.io/debian-stable/jenkins.io-2026.key
chmod a+r "$KEYRING"
echo "deb [signed-by=${KEYRING}] https://pkg.jenkins.io/debian-stable binary/" > "$LIST"

echo "==> installing jenkins"
apt-get update -qq
apt-get install -y jenkins

echo "==> enabling jenkins at boot and starting now"
systemctl daemon-reload
systemctl enable jenkins
systemctl restart jenkins

echo ""
echo "==> status"
systemctl --no-pager status jenkins || true

echo ""
echo "done. open http://localhost:8080"
echo "unlock password:"
echo "  sudo cat /var/lib/jenkins/secrets/initialAdminPassword"
echo ""
echo "logs: journalctl -u jenkins -f"
