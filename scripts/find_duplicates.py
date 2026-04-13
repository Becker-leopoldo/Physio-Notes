import re
from collections import Counter
import os

def find_duplicates(file_path):
    if not os.path.exists(file_path):
        print(f"Erro: Arquivo não encontrado - {file_path}")
        return
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Busca strings entre aspas duplas ou simples
    strings = re.findall(r'\"([^\"]+)\"|\'([^\']+)\'', content)
    c = Counter(s[0] or s[1] for s in strings)
    
    print(f"\nDuplicações em: {os.path.basename(file_path)}")
    print("-" * 30)
    # Filtra strings pequenas (menos de 4 caracteres) ou que aparecem pouco
    for k, v in sorted(c.items(), key=lambda item: item[1], reverse=True):
        if v >= 3 and len(k) > 5:
            print(f"[{v}x] '{k}'")

if __name__ == "__main__":
    # Tenta localizar o diretório base
    base_dir = "g:\\Meu Drive\\Dev\\Poc\\Physio Notes"
    files = [
        "backend/main.py",
        "backend/database.py"
    ]
    for f in files:
        find_duplicates(os.path.join(base_dir, f))
