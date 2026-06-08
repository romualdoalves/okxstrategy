from backend.bot_manager import manager

for bid, inst in manager._instances.items():
    print(f"ID: {bid} | Nome: {inst._bot.name} | SL: {inst._sl_price}")
