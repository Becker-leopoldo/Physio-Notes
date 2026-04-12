import requests
import json
import os
import sys
from pathlib import Path

URL = "https://sonarcloud.io/api/issues/search"


def _load_env_file():
    """Carrega variáveis do .env na raiz do projeto (dois níveis acima de scripts/)."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def export_issues():
    _load_env_file()

    token = os.getenv("SONAR_TOKEN")
    if not token and len(sys.argv) > 1:
        token = sys.argv[1]

    if not token:
        print("Erro: SONAR_TOKEN não encontrado. Adicione ao .env ou use: python export_sonar.py <SEU_TOKEN>")
        return

    project_key = "Becker-leopoldo_Physio-Notes"
    issues = []
    seen_keys = set()
    page = 1
    page_size = 500

    print("-" * 50)
    print(f"Buscando issues do projeto: {project_key}")

    while True:
        params = {
            "componentKeys": project_key,
            "ps": page_size,
            "p": page,
            "resolved": "false",
        }

        try:
            response = requests.get(URL, params=params, auth=(token, ""))
        except Exception as e:
            print(f"Erro na conexão: {e}")
            return

        if response.status_code != 200:
            print(f"Erro HTTP {response.status_code}: {response.text[:200]}")
            return

        data = response.json()
        batch = data.get("issues", [])
        total = data.get("total", 0)

        for issue in batch:
            if issue["key"] not in seen_keys:
                seen_keys.add(issue["key"])
                issues.append(issue)

        fetched = (page - 1) * page_size + len(batch)
        print(f"  Página {page}: {len(batch)} issues (total Sonar: {total}, baixados: {fetched})")

        if fetched >= total or not batch:
            break

        page += 1

    if not issues:
        print("-" * 50)
        print("Nenhuma issue encontrada. Verifique se o SONAR_TOKEN tem acesso ao projeto.")
        return

    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sonar_issues.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(issues, f, indent=2, ensure_ascii=False)

    print("-" * 50)
    print(f"✅ {len(issues)} issues salvos em {output_path}")
    print("-" * 50)


if __name__ == "__main__":
    export_issues()
