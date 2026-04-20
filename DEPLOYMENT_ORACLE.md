# Oracle Cloud Deployment Guide

## Setup Instructions

### 1. Pull Latest Changes
```bash
cd /path/to/your/project
git pull origin main
```

### 2. Install Dependencies
```bash
# Activate virtual environment
source venv/bin/activate

# Install required packages
pip install yt-dlp nodejs-bin
```

### 3. Make Script Executable
```bash
chmod +x oracle_cloud_youtube.sh
```

### 4. Test YouTube Download
```bash
# Test with a sample video
./oracle_cloud_youtube.sh https://www.youtube.com/watch?v=dQw4w9WgXcQ test_download.mp4
```

## Usage

### Command Line Usage
```bash
# Basic usage
./oracle_cloud_youtube.sh <YouTube_URL>

# With custom filename
./oracle_cloud_youtube.sh <YouTube_URL> my_video.mp4

# Example
./oracle_cloud_youtube.sh https://www.youtube.com/watch?v=zz2a9Q2Wru0 my_video.mp4
```

### Python Integration
```python
import subprocess
import os

def download_youtube_oracle(url, output_filename=None):
    """Download YouTube video using Oracle Cloud optimized method"""
    script_path = os.path.join(os.path.dirname(__file__), 'oracle_cloud_youtube.sh')
    
    cmd = [script_path, url]
    if output_filename:
        cmd.append(output_filename)
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        return True, result.stdout
    else:
        return False, result.stderr

# Example usage in your Discord bot
success, message = download_youtube_oracle("https://www.youtube.com/watch?v=VIDEO_ID", "video.mp4")
if success:
    print("Download successful!")
else:
    print(f"Download failed: {message}")
```

## Key Features

- **Oracle Cloud Optimized**: Uses mobile user agent and multiple clients
- **No Cookies Required**: Works without YouTube authentication
- **720p Quality**: Downloads best quality up to 720p
- **Metadata Support**: Embeds thumbnails and metadata
- **Error Handling**: Robust error handling for cloud environments

## Troubleshooting

### Common Issues

1. **Permission Denied**: 
   ```bash
   chmod +x oracle_cloud_youtube.sh
   ```

2. **Virtual Environment Not Found**:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install yt-dlp nodejs-bin
   ```

3. **Still Getting Bot Errors**:
   - Try different video URLs
   - Some videos may have additional restrictions
   - Wait a few minutes between requests

### Alternative Command
If the script doesn't work, use the direct command:
```bash
source venv/bin/activate && python -m yt_dlp --extractor-args "youtube:player_client=android,web" --format "best[height<=720]" <YouTube_URL>
```

## Integration with Discord Bot

The solution is designed to work seamlessly with your Discord bot. The script handles all the Oracle Cloud-specific configurations so you can focus on your bot logic.
