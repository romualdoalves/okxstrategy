import asyncio, traceback
from backend.main import rank_assets

async def main():
    try:
        res = await rank_assets("B006", "15m")
        print("Success:", res)
    except Exception as e:
        traceback.print_exc()

asyncio.run(main())
