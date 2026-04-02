"""
Rode uma vez para gerar as chaves VAPID e adicionar ao .env:

    python generate_vapid_keys.py

Cole as 3 linhas geradas no seu arquivo .env.
"""
from py_vapid import Vapid

v = Vapid()
v.generate_keys()
pub = v.public_key_urlsafe
priv = v.private_key_urlsafe

print("# Cole no seu .env:")
print(f"VAPID_PUBLIC_KEY={pub}")
print(f"VAPID_PRIVATE_KEY={priv}")
print("VAPID_EMAIL=mailto:seuemail@dominio.com")
