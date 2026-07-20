#!/bin/bash
# 🔥 永久修复脚本 — OrcaTerm粘贴一次，永久解决所有问题
set -e
echo "🚀 Firefight AI 永久部署修复"

# 1. 修复SSH服务
echo "📡 修复SSH..."
sudo systemctl unmask sshd 2>/dev/null || true
sudo systemctl restart sshd
sudo systemctl enable sshd
sudo ufw allow 22/tcp 2>/dev/null || true
sudo iptables -I INPUT -p tcp --dport 22 -j ACCEPT 2>/dev/null || true
echo "✅ SSH已修复并开机自启"

# 2. 配置DeepSeek Key (写入settings.yaml)
echo "🔑 配置API Key..."
cat > /home/ubuntu/firefightAI/config/settings.yaml << 'YAML_EOF'
deepseek:
  api_key: "YOUR_DEEPSEEK_KEY"
  model: "deepseek-chat"
  temperature: 0.7
  max_tokens: 4096

github:
  repo: "chenyt-Indom/firefightAI"
  branch: "master"

server:
  host: "0.0.0.0"
  port: 5000
  cors_origins: ["*"]
YAML_EOF
echo "✅ settings.yaml 已配置"

# 3. 初始化Git仓库 (用于GitHub推送)
echo "📦 配置Git..."
cd /home/ubuntu/firefightAI
if [ ! -d ".git" ]; then
    git init
    git remote add origin git@github.com:chenyt-Indom/firefightAI.git
fi
echo "✅ Git已初始化"

# 4. 创建自动化部署端点 (无需SSH即可更新)
echo "🔧 创建自动部署端点..."
cat > /home/ubuntu/firefightAI/auto_deploy.sh << 'DEPLOY_EOF'
#!/bin/bash
# Webhook触发的自动部署
cd /home/ubuntu/firefightAI
git pull origin master 2>/dev/null || echo "git pull skipped"
sudo systemctl restart firefightai
echo "DEPLOYED at $(date)" >> /tmp/deploy.log
DEPLOY_EOF
chmod +x /home/ubuntu/firefightAI/auto_deploy.sh

# 5. 重建systemd服务 (确保环境变量)
echo "⚙️ 更新systemd..."
sudo tee /etc/systemd/system/firefightai.service > /dev/null << 'SVC_EOF'
[Unit]
Description=Firefight AI Dashboard
After=network.target sshd.service

[Service]
Type=simple
User=root
WorkingDirectory=/home/ubuntu/firefightAI
Environment="DEEPSEEK_API_KEY=YOUR_KEY_HERE"
Environment="GIT_SSH_COMMAND=ssh -o StrictHostKeyChecking=no"
ExecStart=/usr/bin/python3 dashboard_server.py --port 5000 --host 0.0.0.0
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVC_EOF

sudo systemctl daemon-reload
sudo systemctl enable firefightai
sudo systemctl restart firefightai
echo "✅ systemd已更新"

# 6. 等待并验证
sleep 4
echo ""
echo "═══════════════════════════════════"
echo "  验证结果"
echo "═══════════════════════════════════"
echo -n "Dashboard: " && curl -sk -o /dev/null -w "%{http_code}" https://localhost/api/version && echo ""
echo -n "GitHub:    " && curl -sk https://localhost/api/github/status 2>/dev/null | python3 -c "import json,sys;print(json.load(sys.stdin).get('api_status','?'))" 2>/dev/null || echo "checking..."
echo -n "SSH:       " && sudo systemctl is-active sshd
echo -n "自启:      " && sudo systemctl is-enabled sshd && echo "" && sudo systemctl is-enabled firefightai
echo ""
echo "🎉 全部修复完成!"
echo "   https://firefightai.top"
echo "═══════════════════════════════════"
