import json
import os

def analyze():
    path = os.path.join(os.path.dirname(__file__), "sonar_issues.json")
    if not os.path.exists(path):
        print("Arquivo não encontrado.")
        return

    with open(path, "r", encoding="utf-8") as f:
        issues = json.load(f)

    summary = {
        "types": {},
        "severities": {},
        "files_impacted": {}
    }

    priorities = []

    for issue in issues:
        itype = issue.get("type", "UNKNOWN")
        sev = issue.get("severity", "UNKNOWN")
        file = issue.get("component", "").split(":")[-1]
        
        summary["types"][itype] = summary["types"].get(itype, 0) + 1
        summary["severities"][sev] = summary["severities"].get(sev, 0) + 1
        
        if itype in ["BUG", "VULNERABILITY"] or sev in ["BLOCKER", "CRITICAL", "MAJOR"]:
            priorities.append({
                "type": itype,
                "severity": sev,
                "file": file,
                "message": issue.get("message"),
                "line": issue.get("line")
            })
            summary["files_impacted"][file] = summary["files_impacted"].get(file, 0) + 1

    print("-" * 50)
    print("RESUMO SONAR")
    print("-" * 50)
    print(f"Total de Issues: {len(issues)}")
    print(f"Por Tipo: {summary['types']}")
    print(f"Por Severidade: {summary['severities']}")
    print("-" * 50)
    print(f"TOP PRIORIDADES ({len(priorities)}):")
    for p in priorities[:15]: # Show top 15
        print(f"[{p['type']}][{p['severity']}] {p['file']}:{p['line']} -> {p['message']}")
    if len(priorities) > 15:
        print(f"... e mais {len(priorities)-15} itens críticos.")

if __name__ == "__main__":
    analyze()
