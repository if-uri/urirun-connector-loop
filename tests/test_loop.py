from __future__ import annotations
from urirun_connector_loop import core

def test_bindings():
    b=core.urirun_bindings()["bindings"]
    assert "loop://host/policy/query/plan" in b and "loop://host/cycle/command/run" in b

def test_decide_safe_auto_runs_agent(monkeypatch):
    monkeypatch.setattr(core,"_safe_assess",lambda a,t,p:{"verdict":"safe-auto","strategy":"autonomous-worktree"})
    a=core._decide({"id":"T4","missing":[],"ready":True,"readiness":{"status":"open"}},"/x")
    assert a["action"]=="run-agent" and a["safety"]=="safe-auto"

def test_decide_not_safe_auto_is_gated(monkeypatch):
    monkeypatch.setattr(core,"_safe_assess",lambda a,t,p:{"verdict":"needs-human","strategy":"dry-run-verify-human","blockers":["brak verify"]})
    a=core._decide({"id":"T5","missing":[],"ready":True,"readiness":{"status":"open"}},"/x")
    assert a["action"]=="agent-gated" and a["risk"]=="needs-human"

def test_genuine_human_blocker_reasoning():
    # nieusuwalnie ludzkie: link Signal / token PyPI / instalacja na węźle
    assert core._genuine_human_blocker({"name": "Zalinkuj konto Signal na lenovo (skan QR telefonem)"})[0] is True
    assert core._genuine_human_blocker({"name": "Opublikuj signal:// na PyPI", "labels": ["needs-human:pypi-token"]})[0] is True
    assert core._genuine_human_blocker({"name": "Zainstaluj signal-cli na lenovo"})[0] is True
    # AUTO-etykieta na zadaniu kodu/diagnozy → NIE-ludzkie (reframe do agenta)
    assert core._genuine_human_blocker({"name": "DIAGNOZA: X zapętlony", "labels": ["diagnosis", "loop-diag:X"]})[0] is False
    assert core._genuine_human_blocker({"name": "[EWOLUCJA] runtime-lies-ok", "labels": ["evolution", "evolve:runtime-lies-ok"]})[0] is False
    assert core._genuine_human_blocker({"name": "[REFACTOR] no-provenance", "labels": ["refactor", "refactor:no-provenance"]})[0] is False


def test_decide_reframes_auto_human_to_agent(monkeypatch):
    """actor:human auto-nadany na zadaniu KODOWYM NIE ląduje u człowieka — idzie w agent (autonomicznie)."""
    monkeypatch.setattr(core, "_safe_assess", lambda a, t, p: {"verdict": "safe-auto", "strategy": "autonomous-worktree"})
    a = core._decide({"id": "T9", "missing": [], "ready": True, "readiness": {"status": "open"},
                      "name": "[REFACTOR] runtime-lies-ok", "labels": ["actor:human", "refactor", "refactor:runtime-lies-ok"]}, "/x")
    assert a["action"] == "run-agent"          # NIE escalate-human
    assert a.get("reframed")                    # decyzja jest audytowalna (ślad reframe)


def test_decide_keeps_genuine_human(monkeypatch):
    """actor:human z realną zależnością zewn. (link Signal) NADAL trafia do człowieka."""
    a = core._decide({"id": "T10", "missing": [], "ready": True, "readiness": {"status": "open"},
                      "name": "Zalinkuj konto Signal na lenovo (skan QR telefonem)", "labels": ["actor:human", "node:lenovo"]}, "/x")
    assert a["action"] == "escalate-human"


def test_decide_dead_loop_and_creds():
    a=core._decide({"id":"A","missing":["oscylacja 3×"],"readiness":{}},"/x")
    assert a["action"]=="circuit-break"
    a=core._decide({"id":"B","missing":["input operatora / creds"],"readiness":{"status":"open"}},"/x")
    assert a["action"]=="escalate-human"
    assert core._decide({"id":"C","missing":["creds"],"readiness":{"status":"blocked"}},"/x") is None

def test_decide_does_not_run_blocked_or_claimed():
    assert core._decide({"id":"B","missing":[],"ready":True,"readiness":{"status":"blocked"}},"/x") is None
    a=core._decide({"id":"I","missing":["watchdog idle_claim"],"ready":False,"readiness":{"status":"in_progress"}},"/x")
    assert a["action"]=="escalate-human"
    a=core._decide({"id":"M","missing":["execution.state blocked"],"ready":False,
                    "readiness":{"status":"open","execution_state":"blocked"}},"/x")
    assert a["action"]=="escalate-human"

def test_need_capability():
    a=core._decide({"id":"D","missing":["executor dla 'signal' (brak connectora)"],"ready":False,"readiness":{"status":"open"}},"/x")
    assert a["action"]=="spawn-capability-ticket"


def test_decide_routes_kvm_signal_tickets_to_twin_human():
    a = core._decide({
        "id": "T-KVM-SIGNAL",
        "missing": ["Signal działa w trybie mock; realne dostarczenie wymaga signal-cli link"],
        "ready": False,
        "readiness": {"status": "open"},
        "labels": ["signal", "kvm", "lenovo", "signal-gui"],
        "name": "Send signal message",
    }, "/x")
    assert a["action"] == "execute-via-twin-human"


def test_reconcile_does_not_unblock_when_other_blockers_remain(monkeypatch, tmp_path):
    calls=[]
    monkeypatch.setattr(core,"_planfile_bin",lambda:"planfile")
    monkeypatch.setattr(core,"_project",lambda p="":str(tmp_path))
    monkeypatch.setattr(core,"_post_dependency_blockers",lambda p,t:["input operatora / Signal niepodlinkowany"])
    monkeypatch.setattr(core,"_note_once",lambda p,tid,note:calls.append(("note",tid,note)))

    class CP:
        returncode=0
        stderr=""
        stdout='[{"id":"IFURI-001","status":"done"},{"id":"IFURI-002","status":"blocked","outputs":{"notes":["blocked_by IFURI-001"]}}]'

    def fake_run(cmd, **kwargs):
        calls.append(tuple(cmd))
        return CP()

    monkeypatch.setattr("subprocess.run",fake_run)
    assert core._reconcile_deps("/x")==[]
    assert not any("--status" in c and "open" in c for c in calls if isinstance(c, tuple))
    assert any(c[0]=="note" and c[1]=="IFURI-002" for c in calls)

def test_reconcile_uses_fresh_blocked_by_before_unblock(monkeypatch, tmp_path):
    calls=[]
    monkeypatch.setattr(core,"_planfile_bin",lambda:"planfile")
    monkeypatch.setattr(core,"_project",lambda p="":str(tmp_path))
    monkeypatch.setattr(core,"_post_dependency_blockers",lambda p,t:[])
    monkeypatch.setattr(core,"_note_once",lambda p,tid,note:calls.append(("note",tid,note)))

    def fake_show(project, tid):
        if tid=="IFURI-039":
            return {"id":tid,"status":"blocked","blocked_by":["IFURI-045","IFURI-046"],"outputs":{"notes":[]}}
        if tid=="IFURI-045":
            return {"id":tid,"status":"blocked"}
        if tid=="IFURI-046":
            return {"id":tid,"status":"blocked"}
        return {"id":tid,"status":"done"}

    monkeypatch.setattr(core,"_show",fake_show)

    class CP:
        returncode=0
        stderr=""
        stdout='[{"id":"IFURI-041","status":"done"},{"id":"IFURI-045","status":"blocked"},{"id":"IFURI-046","status":"blocked"},{"id":"IFURI-039","status":"blocked","outputs":{"notes":["blocked_by IFURI-041"]}}]'

    def fake_run(cmd, **kwargs):
        calls.append(tuple(cmd))
        return CP()

    monkeypatch.setattr("subprocess.run",fake_run)
    assert core._reconcile_deps("/x")==[]
    assert not any("--status" in c and "open" in c for c in calls if isinstance(c, tuple))

def test_cycle_dry_run_never_mutates(monkeypatch):
    monkeypatch.setattr(core,"_gap_scan",lambda p:{"tickets":[{"id":"A","missing":["oscylacja 2×"],"ready":False,"readiness":{}}],"systemic":[]})
    r=core.cycle_command_run(project="/x",apply=False)
    assert r["ok"] and r["dry_run"] and r["applied"]==[]

def test_cycle_apply_defines_goal_freeze(monkeypatch):
    monkeypatch.setattr(core, "_reap_stale_in_progress", lambda p: [])
    monkeypatch.setattr(core, "_reconcile_deps", lambda p: [])
    monkeypatch.setattr(core, "_triage_cleanup", lambda p: [])
    monkeypatch.setattr(core, "_rabbit_hole_react", lambda p: [])
    monkeypatch.setattr(core, "_goal_freeze", lambda: False)
    monkeypatch.setattr(core, "_evolve_reflect", lambda p: [])
    monkeypatch.setattr(core, "_backlog_refill", lambda p: [])
    monkeypatch.setattr(core, "plan", lambda p: {"actions": [], "by_risk": {}, "systemic": [], "total": 0})
    r = core.cycle_command_run(project="/x", apply=True)
    assert r["ok"] and r["dry_run"] is False

def test_gated_run_agent_not_applied_without_auto(monkeypatch):
    monkeypatch.setattr(core,"_gap_scan",lambda p:{"tickets":[{"id":"A","missing":[],"ready":True,"readiness":{"status":"open"}}],"systemic":[]})
    monkeypatch.setattr(core,"_safe_assess",lambda a,t,p:{"verdict":"safe-auto","strategy":"autonomous-inplace"})
    r=core.cycle_command_run(project="/x",apply=True,auto_agent=False)
    assert r["applied"][0]["applied"] is False and "auto_agent=False" in r["applied"][0]["why"]

def test_multiagent_match():
    agents=[{"id":"host-code","node":"nvidia","caps":["code"]},{"id":"node-lenovo","node":"lenovo","caps":["signal","kvm"]}]
    assert core._match({"node":"nvidia","cap":"code"},agents)=="host-code"
    assert core._match({"node":"lenovo","cap":"signal"},agents)=="node-lenovo"
    assert core._match({"node":"lenovo","cap":"code"},agents) is None  # brak code na lenovo

def test_actor_routing():
    assert core._ticket_actor({"labels":["actor:human","node:lenovo"]})=="human"
    assert core._ticket_actor({"labels":["actor:koru"]})=="koru"
    assert core._ticket_actor({"labels":["code"]})=="agent"  # default
    # human > koru priorytet
    assert core._ticket_actor({"labels":["actor:koru","actor:human"]})=="human"
