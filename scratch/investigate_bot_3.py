from backend.database import SessionLocal, TradeModel, BotModel
import json

db = SessionLocal()
bots = db.query(BotModel).all()
for bot in bots:
    print(f"Bot ID: {bot.id}, Name: {bot.name}, Asset: {bot.symbol}")
    trades = db.query(TradeModel).filter(TradeModel.bot_id == bot.id).all()
    for t in trades:
        d = vars(t).copy()
        d.pop('_sa_instance_state', None)
        print(f"  Trade: {d['type']} {d['direction']} {d['pnl']}")
