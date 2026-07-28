#!/usr/bin/env bash
# Sets up an Ubuntu VPS to run the Telegram hosting bot + host Node.js/Java/static projects.
set -e

echo "==> Updating packages"
sudo apt update && sudo apt upgrade -y

echo "==> Installing Python3, pip, venv"
sudo apt install -y python3 python3-pip python3-venv

echo "==> Installing Node.js 20.x (needed to run/host Node projects, and for PM2)"
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

echo "==> Installing PM2 (process manager for hosted Node/Java apps)"
sudo npm install -g pm2

echo "==> Installing Java (JDK) + Maven + Gradle for Java project support"
sudo apt install -y default-jdk maven gradle

echo "==> Installing unzip"
sudo apt install -y unzip

echo "==> Creating hosted-apps directory"
sudo mkdir -p /var/www/hosted
sudo chown -R "$USER":"$USER" /var/www/hosted

echo "==> Setting up Python virtual environment"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo ""
echo "Done. Next steps:"
echo "  1) cp .env.example .env   and fill in BOT_TOKEN, PUBLIC_BASE_URL, etc."
echo "  2) source venv/bin/activate"
echo "  3) pm2 start bot.py --name hosting-bot --interpreter venv/bin/python3"
echo "  4) pm2 start webserver.py --name hosting-web --interpreter venv/bin/python3"
echo "  5) pm2 save && pm2 startup"
