
import yt_dlp
import os

YDL_OPTIONS_FAST = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch1',
    'quiet': False,
    'no_warnings': False,
    'js_runtimes': {'node': {}},
    'force_ipv4': True,
    'retries': 0,
    'cookiefile': '/home/win-htut/discordbot/cookies.txt',
}

def test():
    print(f"yt-dlp version: {yt_dlp.version.__version__}")
    ydl = yt_dlp.YoutubeDL(YDL_OPTIONS_FAST)
    try:
        # Using the same search query that failed
        info = ydl.extract_info("ytsearch1:myo gyi", download=False)
        print("SUCCESS")
    except Exception as e:
        print(f"FAILED: {e}")

if __name__ == "__main__":
    test()
