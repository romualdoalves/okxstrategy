import sys; sys.path.append('.')
from backend.database import SessionLocal, BotModel
db = SessionLocal()
bot = db.query(BotModel).filter(BotModel.name.like("%Bot19%")).first()
if bot:
    print(f"Parâmetros do {bot.name}:")
    print(bot.strategy_params)
else:
    print("Bot não encontrado")
db.close()
