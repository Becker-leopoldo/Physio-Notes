import base64, httpx, time, json

IMG_PATH = "g:/Meu Drive/Dev/Poc/Rede Américas/Reconhecimento Facial/backend/imagens/foto_3_f4fe14.jpg"
CPF = "25774435016"
API = "http://localhost:8000"

with open(IMG_PATH, "rb") as f:
    foto_b64 = base64.b64encode(f.read()).decode()

print(f"Imagem: {len(foto_b64)} chars base64")

r = httpx.post(f"{API}/consulta/datavalid/facial", json={"cpf": CPF, "foto": foto_b64}, timeout=30)
print(f"POST status: {r.status_code}")
print(f"POST body: {r.text[:300]}")

data = r.json()
if "job_id" not in data:
    print("ERRO: sem job_id na resposta")
    exit(1)

job_id = data["job_id"]
print(f"Job: {job_id}")

for i in range(30):
    time.sleep(5)
    r = httpx.get(f"{API}/job/{job_id}", timeout=10)
    status = r.json()["status"]
    print(f"[{i+1}] {status}")
    if status in ("done", "error"):
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))
        break
