#!/usr/bin/env python3
"""
Script para aplicar correções no bot_manager.py no servidor.
Execute diretamente no servidor:
    cd /opt/okx-strategy && python3 apply_fixes.py
"""

import re
import shutil
from datetime import datetime

FILE = "/app/backend/bot_manager.py"
BACKUP = f"/app/backend/bot_manager.py.bak.{int(datetime.now().timestamp())}"

def read_file():
    with open(FILE, "r") as f:
        return f.read()

def write_file(content):
    with open(FILE, "w") as f:
        f.write(content)

def apply_fixes():
    content = read_file()
    original = content
    
    # Backup
    shutil.copy2(FILE, BACKUP)
    print(f"[1/4] Backup criado: {BACKUP}")
    
    # Fix 1: _recover_state() - sempre recria trailing stop
    # Remove a condição if last_trade.tp1_done:
    old_pattern = r'''                # Restaura estado da Sombra Dinâmica — evita depender do ticker after-hours
                if last_trade\.tp1_done:
                    self\._tp1_done          = True
                    self\._ts_algo_id        = "sw"
                    self\._ts_callback_ratio = 0\.005  # mínimo seguro; force_sync_trailing recalcula
                    if self\._sl_price > 0 and self\._ts_callback_ratio > 0:
                        if self\._direction == 1:
                            self\._peak_price = self\._sl_price / \(1 - self\._ts_callback_ratio\)
                        else:
                            self\._peak_price = self\._sl_price / \(1 \+ self\._ts_callback_ratio\)
                    # Marca o nível já persistido para evitar re-escrita desnecessária
                    self\._last_persisted_sl = self\._sl_price
                    log\.info\("\[Bot %d\] Estado recuperado do banco: %s @ %\.2f \(Trade ID: %d\) — Sombra reativada \(sl=%\.4f peak=%\.4f\)",
                             self\.config\.id, last_trade\.direction, self\._entry_price,
                             self\._current_trade_id, self\._sl_price, self\._peak_price\)
                else:
                    log\.info\("\[Bot %d\] Estado recuperado do banco: %s @ %\.2f \(Trade ID: %d\)",
                             self\.config\.id, last_trade\.direction, self\._entry_price, self\._current_trade_id\)'''
    
    new_code = '''                # Restaura estado da Sombra Dinâmica — sempre que houver posição aberta,
                # independente de TP1 ter sido atingido ou não. O trailing stop é proteção
                # primária, não secundária ao TP1.
                self._tp1_done          = bool(last_trade.tp1_done)
                self._ts_algo_id        = "sw"
                self._ts_callback_ratio = 0.005  # mínimo seguro; force_sync_trailing recalcula
                if self._sl_price > 0 and self._ts_callback_ratio > 0:
                    if self._direction == 1:
                        self._peak_price = self._sl_price / (1 - self._ts_callback_ratio)
                    else:
                        self._peak_price = self._sl_price / (1 + self._ts_callback_ratio)
                # Marca o nível já persistido para evitar re-escrita desnecessária
                self._last_persisted_sl = self._sl_price
                log.info("[Bot %d] Estado recuperado do banco: %s @ %.2f (Trade ID: %d) — Sombra reativada (sl=%.4f peak=%.4f tp1_done=%s)",
                         self.config.id, last_trade.direction, self._entry_price,
                         self._current_trade_id, self._sl_price, self._peak_price,
                         self._tp1_done)'''
    
    content = re.sub(old_pattern, new_code, content)
    
    # Fix 2: Orphan recovery - calcula TP1 (primeira ocorrência)
    content = content.replace(
        '"tp1_price":   0.0,\n                                "source":      "orphan_recovery",',
        '"tp1_price":   orphan_tp1,\n                                "source":      "orphan_recovery",',
        1  # Só a primeira ocorrência
    )
    
    # Fix 3: Segunda ocorrência do orphan recovery
    content = content.replace(
        '"tp1_price":   0.0,\n                        "source":      "orphan_recovery",',
        '"tp1_price":   orphan_tp1,\n                        "source":      "orphan_recovery",',
        1
    )
    
    if content == original:
        print("[AVISO] Nenhuma mudança foi aplicada. O arquivo já pode estar corrigido.")
        return False
    
    write_file(content)
    print("[2/4] Correções aplicadas")
    return True

def verify_syntax():
    import py_compile
    try:
        py_compile.compile(FILE, doraise=True)
        print("[3/4] Sintaxe OK")
        return True
    except py_compile.PyCompileError as e:
        print(f"[ERRO] Erro de sintaxe: {e}")
        return False

def main():
    print("========================================")
    print("  Aplicando correções no bot_manager.py")
    print("========================================")
    
    if apply_fixes():
        if verify_syntax():
            print("[4/4] Sucesso! Reinicie o container:")
            print("      docker restart okx_strategy")
        else:
            print("[ERRO] Sintaxe inválida. Restaurando backup...")
            shutil.copy2(BACKUP, FILE)
            print("      Backup restaurado.")
    else:
        print("[4/4] Nenhuma mudança necessária.")
    
    print("========================================")

if __name__ == "__main__":
    main()
