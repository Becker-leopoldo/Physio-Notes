import httpx, time, json

CPF = "25774435016"
INSCRICAO_COREN = "17168"
API = "http://localhost:8000"

payload = {
    "cpf": CPF,
    "nome": "Teste Homologacao",
    "inscricao_coren": INSCRICAO_COREN,
}

r = httpx.post(f"{API}/consulta/admissao", json=payload, timeout=30)
print(f"POST status: {r.status_code}")
print(f"POST body: {r.text[:300]}")

data = r.json()
if "job_id" not in data:
    print("ERRO: sem job_id na resposta")
    exit(1)

job_id = data["job_id"]
print(f"Job: {job_id}")

for i in range(60):
    time.sleep(5)
    r = httpx.get(f"{API}/job/{job_id}", timeout=10)
    status = r.json()["status"]
    print(f"[{i+1}] {status}")
    if status in ("done", "error"):
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))
        break
