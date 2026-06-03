# EC2 systemd deployment (Kotak)

## One-time setup on the instance

```bash
cd /home/ec2-user
git clone https://github.com/rajeshbagavathy/bot-lite-kotak.git
cd bot-lite-kotak

# Kotak Neo SDK is vendored in-repo (Kotak-neo-api-v2/neo_api_client/)
./scripts/setup_local.sh
cp deploy/bot.env.example deploy/bot.env
# Edit deploy/bot.env if paths/ports differ
```

**IAM role** on the instance must allow `ssm:GetParameter` on:

- `/trade/config/5pindra/loginusername`
- `/trade/config/5pindra/loginpassword`
- `/trade/config/kotak/rajesh/*`

**Security group:** allow inbound TCP **8002** (must match `WEB_UI_PORT` in `deploy/bot.env`).

## Install systemd unit

```bash
sudo cp deploy/bot-lite-kotak.service /etc/systemd/system/bot-lite-kotak.service
# Edit paths in the unit if your clone is not under /home/ec2-user/bot-lite-kotak

sudo systemctl daemon-reload
sudo systemctl enable bot-lite-kotak
sudo systemctl start bot-lite-kotak
sudo systemctl status bot-lite-kotak
journalctl -u bot-lite-kotak -f
```

## After each EC2 start

1. Open `http://<ec2-public-ip>:8002/dashboard` (basic auth from SSM `5pindra` login paths).
2. Enter **today’s Kotak TOTP** once in the modal.
3. Refresh — modal should not appear again until the next bot restart or new IST day.
