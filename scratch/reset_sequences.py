import os
import sqlalchemy
from dotenv import load_dotenv

load_dotenv('.env', encoding='utf-8')
db_url = os.environ.get('DATABASE_URL')
engine = sqlalchemy.create_engine(db_url.replace('postgresql://', 'postgresql+psycopg://'))

tables_and_seqs = [
    ("bots", "bots_id_seq"),
    ("trades", "trades_id_seq"),
    ("snapshots", "snapshots_id_seq"),
    ("signal_logs", "signal_logs_id_seq"),
    ("trade_reports", "trade_reports_id_seq"),
    ("ai_analysis_logs", "ai_analysis_logs_id_seq")
]

try:
    with engine.connect() as conn:
        print("PG Connected. Resetting primary key sequences...")
        for table, seq in tables_and_seqs:
            # Check max id
            res = conn.execute(sqlalchemy.text(f"SELECT COALESCE(MAX(id), 0) FROM {table}"))
            max_id = res.scalar()
            
            # Reset sequence to max_id
            # setval(sequence, value, is_called). If is_called is false, the next nextval will return value, else value+1.
            # We set it to max(max_id, 1) and if max_id is 0 we set is_called=false
            if max_id == 0:
                conn.execute(sqlalchemy.text(f"SELECT setval('{seq}', 1, false)"))
            else:
                conn.execute(sqlalchemy.text(f"SELECT setval('{seq}', {max_id}, true)"))
            
            print(f"Sequence '{seq}' reset to {max_id}")
        
        # Commit transaction explicitly
        conn.commit()
        print("All sequences reset successfully!")
except Exception as e:
    print(f"Error resetting sequences: {e}")
