import asyncio
from music import get_stream_url

async def test():
    try:
        info = await get_stream_url('never gonna give you up')
        print(f"Success: {info.get('title')}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
