# Oracle Cloud Troubleshooting Guide

## Immediate Steps to Fix YouTube Download Issues

### 1. Pull Latest Changes
```bash
cd /path/to/your/discordbot
git pull origin main
```

### 2. Verify You're Using the Updated Code
```bash
# Check if the music.py file has the latest fixes
grep -n "YDL_OPTIONS_EMERGENCY" music.py
# Should show line around 79

# Check for the prefetch fix
grep -n "Only use original_url or webpage_url" music.py
# Should show line around 439
```

### 3. Restart the Bot Properly

#### If running directly:
```bash
# Kill any existing bot processes
pkill -f "python.*bot.py"

# Start fresh
source venv/bin/activate
python bot.py
```

#### If using systemd service:
```bash
# Find your service name
systemctl list-units --type=service | grep -i bot

# Restart the service
sudo systemctl restart your-bot-service-name

# Check status
sudo systemctl status your-bot-service-name
```

### 4. Test the Fix
```bash
# Test YouTube download directly
source venv/bin/activate
python -c "
import asyncio
import sys
sys.path.append('.')
from music import get_stream_url

async def test():
    try:
        info = await get_stream_url('https://www.youtube.com/watch?v=zz2a9Q2Wru0')
        print(f'SUCCESS: {info.get(\"title\")}')
        return True
    except Exception as e:
        print(f'ERROR: {e}')
        return False

result = asyncio.run(test())
print('Test passed!' if result else 'Test failed!')
"
```

## Common Oracle Cloud Issues

### Issue 1: Old Code Still Running
**Symptoms**: Same errors persist after git pull
**Solution**: 
```bash
# Verify git status
git status
# Should show "Your branch is up to date"

# Check actual file content
head -80 music.py | tail -10
# Should show YDL_OPTIONS_EMERGENCY
```

### Issue 2: Virtual Environment Issues
**Symptoms**: Module not found errors
**Solution**:
```bash
# Recreate virtual environment
rm -rf venv
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install yt-dlp nodejs-bin
```

### Issue 3: Service Not Restarting
**Symptoms**: Changes not taking effect
**Solution**:
```bash
# Check if service exists
systemctl list-unit-files | grep -i bot

# If no service, run directly:
cd /path/to/discordbot
source venv/bin/activate
nohup python bot.py > bot.log 2>&1 &
```

### Issue 4: Environment Variables
**Symptoms**: Different behavior than local
**Solution**:
```bash
# Check environment
env | grep -E "(YTDLP|MUSIC|DISCORD)"

# Set if needed
export YTDLP_COOKIES=""
export MUSIC_TEXT_CHANNEL="music"
```

## Debug Commands

### Check What's Actually Running
```bash
# Find bot processes
ps aux | grep python

# Check network connections
netstat -tulpn | grep :8080

# Check logs
tail -f bot.log
```

### Verify YouTube Access
```bash
# Test standalone script
./oracle_cloud_youtube.sh https://www.youtube.com/watch?v=dQw4w9WgXcQ test.mp4

# Test with yt-dlp directly
source venv/bin/activate
python -m yt_dlp --extractor-args "youtube:player_client=android,web" --format "18" https://www.youtube.com/watch?v=zz2a9Q2Wru0 --no-download
```

## If Still Not Working

### 1. Complete Reset
```bash
cd /path/to/discordbot
git pull origin main
rm -rf venv
python -m venv venv
source venv/bin/activate
pip install yt-dlp nodejs-bin discord.py python-dotenv
python bot.py
```

### 2. Manual Service Creation
If no systemd service exists, create one:
```bash
sudo nano /etc/systemd/system/discordbot.service
```

Content:
```ini
[Unit]
Description=Discord Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/discordbot
Environment=PATH=/home/ubuntu/discordbot/venv/bin
ExecStart=/home/ubuntu/discordbot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable discordbot
sudo systemctl start discordbot
```

### 3. Check Oracle Cloud Security
```bash
# Check if ports are blocked
sudo ufw status

# Check Oracle Cloud firewall (via web console)
# Ensure outbound HTTPS (port 443) is allowed
```

## Quick Test Sequence
Run these commands in order:
```bash
1. git pull origin main
2. source venv/bin/activate
3. python -c "from music import YDL_OPTIONS_EMERGENCY; print('Emergency config found')"
4. python bot.py
```

If step 3 fails, the code isn't updated. If step 4 shows errors, check the logs.
