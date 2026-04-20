#!/bin/bash

# Oracle Cloud YouTube Downloader Script
# This script is optimized for Oracle Cloud environments

# Activate virtual environment
source venv/bin/activate

# Function to download YouTube video
download_youtube() {
    local url="$1"
    local output="$2"
    
    if [ -z "$url" ]; then
        echo "Error: No URL provided"
        return 1
    fi
    
    # Default output filename if not provided
    if [ -z "$output" ]; then
        output="video_%(title)s.%(ext)s"
    fi
    
    echo "Downloading from: $url"
    echo "Output: $output"
    
    # Use multiple clients for better compatibility on Oracle Cloud
    python -m yt_dlp \
        --extractor-args "youtube:player_client=android,web" \
        --user-agent "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36" \
        --format "best[height<=720]" \
        --output "$output" \
        --no-playlist \
        --embed-thumbnail \
        --embed-metadata \
        "$url"
}

# Check if URL is provided
if [ $# -eq 0 ]; then
    echo "Usage: $0 <YouTube_URL> [output_filename]"
    echo "Example: $0 https://www.youtube.com/watch?v=zz2a9Q2Wru0 my_video.mp4"
    exit 1
fi

# Download the video
download_youtube "$1" "$2"
