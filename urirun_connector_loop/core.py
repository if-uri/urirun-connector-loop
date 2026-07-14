# Author: Tom Sapletta · Part of the ifURI solution.
# urirun-connector-loop - ZAMKNIĘTA pętla korekcyjna: OBSERWUJ -> ZDECYDUJ -> DZIAŁAJ.

# Watchdog OBSERWUJE, gap:// obnaża luki - ale dotąd korektę wykonywał CZŁOWIEK (klik). Ten
# connector domyka pętlę: czyta ``gap://scan`` + ``watch://detect``, mapuje stan KAŻDEGO ticketu
# na JEDNĄ poprawną akcję i (z ``apply=True``) ją stosuje - bez ingerencji. Odpalany cyklicznie
# (cron) czyni zdarzenia/luki ŹRÓDŁEM autonomicznych reakcji.

# Polityka bezpieczeństwa:
#   * SAFE (auto): ``circuit-break`` dead-loopów (idempotentne), ``escalate`` needs_input do
#     ``human://`` + blocked (stop jałowego retry) - te NIE zmieniają repo, tylko przerywają
#     bezsensowne pętle i proszą operatora.
#   * RISKY (za bramką ``auto_agent``): ``run-agent`` (zmienia repo) - tylko gdy jawnie włączone.
#   * REPORT (bez akcji): brak acceptance_criteria - sygnalizowane, nie auto-naprawiane.

# Idempotentne: circuit-break ma ``_diag_exists``, escalate pomija już-blocked. Read-only plan
# zawsze bezpieczny; ``cycle/command/run`` mutuje -> isolated.
from __future__ import annotations

import os
from typing import Any

import urirun

CONNECTOR_ID = "loop"
conn = urirun.connector(CONNECTOR_ID, scheme="loop")


def _ok(**kw: Any) -> dict[str, Any]:
    return urirun.ok(connector=CONNECTOR_ID, **kw)


def _fail(msg: str, action: str) -> dict[str, Any]:
    return urirun.fail(msg, connector=CONNECTOR_ID, action=action)


def _project(project: str = "") -> str:
    return project or os.environ.get("URIRUN_KORU_PROJECT") or os.path.expanduser("~/github/if-uri")


def _gap_scan(project: str) -> dict:
    from urirun_connector_continuity import core as gap
    return gap.scan(project)


def _safe_assess(action: str, tid: str, project: str) -> dict | None:
    try:
        from urirun_connector_safety import core as sf
        return sf.assess(action, tid, project)
    except Exception:  # noqa: BLE001
        return None


# Sygnały NIEUSUWALNIE-ludzkie: zależność od świata fizycznego/zewnętrznego, której agent NIE zdejmie.
_HUMAN_IRREDUCIBLE = (
    "signal-cli", "zalinkuj", "skan qr", "pypi", "pypi-token", "token",
    "creds", "secret://", "zainstaluj", "instalacja", "linkuj konto",
)
# Etykiety/źródła świadczące, że actor:human to AUTO-etykieta na zadaniu KODOWYM (agent je zrobi).
_AUTO_HUMAN_LABELS = ("diagnosis", "rabbit-hole", "evolution", "evolve", "refactor", "nxdo", "nxdo-review")
_AUTO_HUMAN_PREFIX = ("loop-diag:", "rabbithole:", "evolve:", "refactor:", "capgen:", "capretry:")


def _genuine_human_blocker(r: dict) -> tuple[bool, str]:
    # """Czy ``actor:human`` jest NIEUSUWALNY (realna zależność zewn./fizyczna) - czy tylko
    # AUTO-etykieta watchdoga na zadaniu KODOWYM, które headless-agent wykona bez człowieka.
    #
    # Sedno redukcji obciążenia operatora: koru/watchdog etykietuje diagnozy ``no_executor`` /
    # rabbit-hole / [EWOLUCJA] jako ``actor:human``, ale to zadania kodu/diagnozy - agent je zrobi.
    # Przy człowieku zostaje TYLKO realny świat zewnętrzny (link Signal, token PyPI, instalacja
    # na węźle, creds). Decyzja jest jawnie uzasadniona (trace), więc reframe jest audytowalny.
    # """
    labels = [str(x).lower() for x in (r.get("labels") or [])]
    text = " ".join([str(r.get("name") or ""), str(r.get("description") or ""),
                     " ".join(r.get("missing") or [])]).lower()
    if any(l.startswith("needs-human:") for l in labels):
        return True, "jawna etykieta needs-human:* - wymóg operatora"
    if any(k in text for k in _HUMAN_IRREDUCIBLE):
        # exception: kvm on lenovo is delegable to digital twin (urirun-twin-human)
        if "kvm" in labels or "lenovo" in labels or "node:lenovo" in text:
            return False, "kvm/lenovo - delegowalne do twin-human (nie wymaga realnego człowieka)"
        return True, "zależność zewnętrzna/fizyczna (link/token/instalacja/creds) - nieusuwalna przez agenta"
    auto = any(l in labels for l in _AUTO_HUMAN_LABELS) or \
        any(l.startswith(_AUTO_HUMAN_PREFIX) for l in labels)
    if auto:
        return False, "actor:human = AUTO-etykieta watchdoga na zadaniu kodu/diagnozy -> agent wykona (reframe)"
    return True, "brak sygnału reframe - zachowawczo traktuj jako wymóg człowieka"


def _kvm_signal_ticket(r: dict) -> bool:
    labels = [str(x).lower() for x in (r.get("labels") or [])]
    text = " ".join([str(r.get("name") or ""), str(r.get("description") or ""), " ".join(r.get("missing") or [])]).lower()
    if "signal" not in text and "signal" not in labels:
        return False
    if any(l in labels for l in ("kvm", "lenovo", "signal-gui", "signal-gui-kvm")):
        return True
    if any(k in text for k in ("zalinkuj", "link", "qr", "skan", "konto", "podlink")):
        return False
    return any(k in text for k in ("signal-gui", "signal gui", "kvm", "lenovo"))


def _decide_ready(r: dict, tid: Any, project: str) -> dict | None:
#     """Ticket GOTOWY -> wybór aktora/ścieżki: human (tylko nieusuwalnie) vs koru vs autonomiczny agent.
#     Auto-etykieta actor:human na zadaniu kodu jest REFRAME'owana do agenta (sedno redukcji człowieka)."""
    rd = r.get("readiness") or {}
    labels = [str(x).lower() for x in (r.get("labels") or [])]
    if "actor:human" in labels:
        genuine, why = _genuine_human_blocker(r)
        if genuine:
            if rd.get("status") == "blocked":
                return None
            return {"ticket": tid, "action": "escalate-human", "risk": "safe",
                    "reason": f"krok wymaga człowieka: {why}"}
        r = {**r, "_reframed_from": "actor:human", "_reframe_reason": why}
    if "actor:koru" in labels:
        return {"ticket": tid, "action": "route-koru", "risk": "report",
                "reason": "krok dla koru (publikacja/deploy) -> koru://queue"}
    labels = [str(x).lower() for x in (r.get("labels") or [])]
    if any(l in labels for l in ("kvm", "lenovo", "signal-gui")) or "na lenovo" in (r.get("description","") + r.get("name","")).lower():
        return {"ticket": tid, "action": "execute-via-twin-human", "risk": "safe",
                "reason": "kvm/lenovo desktop action - delegate to urirun-twin-human (real GUI control + logs)"}
    # PRZED wykonaniem: pytaj silnik zaufania. safe-auto ⇒ wykonaj; inaczej ⇒ strategia fallback.
    reframe = {"reframed": r["_reframe_reason"]} if r.get("_reframed_from") else {}
    sa = _safe_assess("run-agent", tid, project)
    if sa and sa.get("verdict") == "safe-auto":
        return {"ticket": tid, "action": "run-agent", "risk": "repo-change",
                "safety": "safe-auto", "strategy": sa.get("strategy"), **reframe,
                "reason": f"gotowy + BEZPIECZNY ({sa.get('strategy')}: odwracalne+weryfikowalne+izolowane) - autonomicznie"}
    strat = (sa or {}).get("strategy", "dry-run-verify-human")
    blk = "; ".join((sa or {}).get("blockers", []))[:70]
    return {"ticket": tid, "action": "agent-gated", "risk": "needs-human",
            "safety": (sa or {}).get("verdict", "unknown"), "strategy": strat, **reframe,
            "reason": f"gotowy, ale NIE safe-auto -> {strat}" + (f"; {blk}" if blk else "")}


_FALLTHROUGH = object()  # sentinel: _decide_stuck nie dał werdyktu -> kontynuuj analizę


def _decide_stuck(r: dict, tid: Any, status: str, execution_state: str,
                  missing: str, rd: dict) -> Any:
#     """Stany NIE-gotowe: niespójność status/state, blocked, claim/watchdog, oscylacja, creds.
#     Zwraca akcję (dict), None (terminal - już blocked, nie spamuj retry) lub ``_FALLTHROUGH``."""
    if execution_state in ("blocked", "waiting_input", "in_progress", "claimed") and execution_state != status:
        return {"ticket": tid, "action": "escalate-human", "risk": "safe",
                "reason": f"niespójny status/execution.state={execution_state} - zsynchronizuj do blocked"}
    if status == "blocked":
        return None  # już stoi na blokadzie; nie planuj retry ani raport-spamu
    if status in ("in_progress", "claimed") or "watchdog" in missing:
        return {"ticket": tid, "action": "escalate-human", "risk": "safe",
                "reason": "claim/watchdog bez potwierdzonego postępu - zablokuj i przerwij retry"}
    if "oscylacja" in missing:
        return {"ticket": tid, "action": "circuit-break", "risk": "safe",
                "reason": "dead-loop - diagnoza zamiast jałowego retry"}
    if "creds" in missing or "input operatora" in missing:
        if rd.get("status") == "blocked":
            return None  # już zablokowany/eskalowany - nie spamuj
        return {"ticket": tid, "action": "escalate-human", "risk": "safe",
                "reason": "needs_input - eskalacja do operatora + stop retry"}
    return _FALLTHROUGH


def _decide(r: dict, project: str = "") -> dict | None:
#     """Stan ticketu (z gap analyze) -> JEDNA poprawna akcja + ryzyko. Kolejność ma znaczenie."""
    tid = r.get("id")
    missing = " ".join(r.get("missing") or []).lower()
    rd = r.get("readiness") or {}
    status = str(rd.get("status") or "").lower()
    execution_state = str(rd.get("execution_state") or "").lower()
    stuck = _decide_stuck(r, tid, status, execution_state, missing, rd)
    if stuck is not _FALLTHROUGH:
        return stuck
    if _kvm_signal_ticket(r) and status not in ("blocked", "in_progress", "claimed"):
        return {"ticket": tid, "action": "execute-via-twin-human", "risk": "safe",
                "reason": "kvm/lenovo Signal GUI — delegate to urirun-twin-human (bypass signal-cli mock block)"}
    if r.get("ready"):
        return _decide_ready(r, tid, project)
    if "acceptance_criteria" in missing:
        return {"ticket": tid, "action": "report-missing-criteria", "risk": "report",
                "reason": "brak Definition of Done - verify:// nie ma czego sprawdzić"}
    if "executor dla" in missing or "brak connectora" in missing:
        if rd.get("status") == "blocked":
            return None  # już zablokowany (czeka na generację connectora) - nie spawnuj kolejnego chaina
        # WSZYSTKO PRZEZ TICKETY: nie generuj inline - UTWÓRZ ticket "wygeneruj connector X",
        # który sam przejdzie cykl (run-agent->verify). Tworzenie ticketu jest bezpieczne (odwracalne).
        return {"ticket": tid, "action": "spawn-capability-ticket", "risk": "safe",
                "reason": "brak wykonawcy -> utwórz TICKET generacji connectora (self-extension przez ticket)"}
    return None


def _agents() -> list[dict]:
#     """Rejestr dostępnych agentów: {id, node, caps}. Host=code-agent; węzły=app/kvm (po capability)."""
    agents = []
    try:
        from urirun_connector_agents import core as ag
        tools = [k for k, v in ag._available().items() if not (ag._ADAPTERS.get(k) or {}).get("gui")]
        if tools:
            agents.append({"id": "host-code", "node": os.uname().nodename,
                           "caps": ["code", "connector-gen"], "tools": tools})
    except Exception:  # noqa: BLE001
        pass
    # Węzły APP (deklaratywnie; rozbudowa = dopisz węzeł/capability, nie kod matchera)
    for node, caps in (("lenovo", ["kvm", "app", "signal", "email"]),):
        agents.append({"id": f"node-{node}", "node": node, "caps": caps})
    # filter digital twins that are enabled (respect disable in list)
    try:
        from urirun.host import ticket_meta
        persons = [p for p in ticket_meta.load_digital_persons() if p.get("_is_enabled", p.get("enabled", True))]
        # could add more agent entries from enabled persons
    except: pass
    return agents


def _ticket_need(t: dict) -> dict:
#     """Czego ticket potrzebuje: node (z meta/opisu) + capability (label/scheme)."""
    import re
    labels = [str(x).lower() for x in (t.get("labels") or [])]
    node = ""
    for lab in labels:
        if lab.startswith("node:"):
            node = lab.split(":", 1)[1]
    blob = (str(t.get("name", "")) + " " + str(t.get("description", ""))).lower()
    if not node:
        node = next((n for n in ("lenovo", "nvidia") if n in blob or n in labels), "")
    cap = "code" if ("code" in labels or "connector-gen" in labels) else ""
    if not cap:
        m = re.search(r"\b([a-z]+)://", blob)
        cap = m.group(1) if m else next((c for c in ("signal", "email", "kvm") if c in labels or c in blob), "")
    return {"node": node, "cap": cap}


def _match(need: dict, agents: list[dict]) -> str | None:
#     """Dopasuj ticket do agenta po node (jeśli wymagany) i capability."""
    for a in agents:
        if need["node"] and need["node"] != a["node"]:
            continue
        if need["cap"] and need["cap"] not in a["caps"]:
            continue
        return a["id"]
    return None


def _available_schemes() -> set[str]:
#     """Zdolności realnie serwowane (grounding dla mind) - schematy z /routes węzłów."""
    import json
    import urllib.request
    schemes: set[str] = set()
    for url in ("http://127.0.0.1:8797", "http://192.168.188.201:8765"):
        try:
            with urllib.request.urlopen(url + "/routes", timeout=4) as r:  # noqa: S310
                d = json.loads(r.read().decode())
                routes = d.get("routes", d)
                for x in (routes if isinstance(routes, list) else []):
                    u = x.get("uri", "") if isinstance(x, dict) else str(x)
                    if "://" in u:
                        schemes.add(u.split("://", 1)[0])
        except Exception:  # noqa: BLE001
            continue
    return schemes


def _mind_advice(intent: str, available: set[str] | None = None) -> dict | None:
#     """FALLBACK DO ŚWIADOMOŚCI (urirun-mind): znane-dobre PRZED LLM + antywzorce + graf zdolności.
#     strategy_selector.select -> source=skill|episode (known-good) albo fresh (planuj); grounded env."""
    try:
        from urirun_mind import strategy_selector
    except Exception:  # noqa: BLE001
        return None
    env = {"available": sorted(available if available is not None else _available_schemes())}
    try:
        sel = strategy_selector.select(intent, prompt=intent, environment=env)
    except Exception:  # noqa: BLE001
        return None
    return {"source": sel.get("source"), "known_good": sel.get("source") in ("skill", "episode"),
            "flow": [s.get("uri") if isinstance(s, dict) else s for s in (sel.get("flow") or [])][:5],
            "antipatterns": sel.get("antipatterns") or [], "note": sel.get("note", "")}


def _capability_acquire(scheme: str) -> dict | None:
#     """Gdy registry pusty dla zdolności - mind.capability_graph: JAK ją zdobyć (chain sub-capability)."""
    try:
        from urirun_mind import capability_graph
    except Exception:  # noqa: BLE001
        return None
    try:
        r = capability_graph.resolve(scheme, _available_schemes())
        return r if r.get("known") else None
    except Exception:  # noqa: BLE001
        return None


def _open_actionable_count(project: str) -> int:
#     """Ile open, nie-blocked, nie-diagnoza ticketów (realna praca dla koru)."""
    import json as _j
    import subprocess
    pf = os.path.expanduser("~/github/if-uri/venv/bin/planfile")
    try:
        cp = subprocess.run([pf, "ticket", "list", "--format", "json"], capture_output=True, text=True, timeout=15, cwd=project)
        data = _j.loads(cp.stdout[cp.stdout.index("["):cp.stdout.rindex("]") + 1])
        return sum(1 for t in data if t.get("status") == "open" and not str(t.get("name", "")).startswith("DIAGNOZA:"))
    except Exception:  # noqa: BLE001
        return 99


def _backlog_refill(project: str, threshold: int = 2) -> list:
    # GENERATOR (semcod nxdo): gdy koru idle (< threshold open) -> nxdo plan (git+LLM) -> tickety.
    # Koru nigdy nie stoi bezczynnie - używa istniejącego nxdo, nie własnego kodu.
    # Long-term: ograniczamy mikro-tickety. ... Dedup + limit = 3 max.
    if _open_actionable_count(project) >= threshold:
        return []
    import json as _j
    import subprocess
    nxdo = os.path.expanduser("~/github/semcod/nxdo/.venv/bin/nxdo")
    if not os.path.exists(nxdo):
        return []
    env = dict(os.environ)
    model = "google/gemini-2.5-flash"
    try:
        from urirun.host.env_loader import load_project_env, nxdo_model as _nxdo_model
        load_project_env(project)
        env = dict(os.environ)
        model = _nxdo_model()
    except Exception:  # noqa: BLE001
        pass
    if "OPENAI_API_KEY" not in env:
        try:
            for line in open(os.path.join(project, "urirun/.env"), encoding="utf-8"):
                if line.startswith("OPENROUTER_API_KEY="):
                    env["OPENAI_API_KEY"] = line.split("=", 1)[1].strip()
                    break
        except OSError:
            pass
    try:
        cp = subprocess.run([nxdo, "plan", "-m", model,
                             "--base-url", "https://openrouter.ai/api/v1", "--max-commits", "10", "--json"],
                            capture_output=True, text=True, timeout=180, cwd=project, env=env)
        d = _j.loads(cp.stdout[cp.stdout.index("{"):cp.stdout.rindex("}") + 1])
    except Exception:  # noqa: BLE001
        return []
    pf = os.path.expanduser("~/github/if-uri/venv/bin/planfile")
    created = []
    # Long-term: limit small nxdo tickets. Prefer fewer, higher-signal items or updates to existing.
    # Many tiny "[NXDO] Review ..." create noise and blocked queue. Consolidate toward goals/backlog.
    try:
        cp_list = subprocess.run([pf, "ticket", "list", "--format", "json"], capture_output=True, text=True, timeout=10, cwd=project)
        existing = _j.loads(cp_list.stdout[cp_list.stdout.index("["):cp_list.stdout.rindex("]") + 1]) if "[" in cp_list.stdout else []
        open_nxdo = [e for e in existing if e.get("status") not in ("done","closed","cancelled") and "nxdo" in str(e.get("labels") or [])]
        if len(open_nxdo) >= 5:
            return []  # already enough nxdo work; don't flood with micro-tickets
    except Exception:
        pass

    tasks = (d.get("tasks") or d.get("plan") or [])[:3]  # reduced from 6 -> higher quality
    for t in tasks:
        base = str(t.get("title") or t.get("name") or "")[:60]
        if len(base) < 8:
            continue
        name = "[NXDO] " + base
        # dedup: if very similar open nxdo ticket exists, skip (or could update desc)
        try:
            if any(base.lower()[:30] in str(e.get("name","")).lower() for e in open_nxdo):
                continue
            subprocess.run([pf, "ticket", "create", name, "-p", "normal", "--source", "nxdo-generator",
                            "-l", "nxdo", "-l", "nxdo-review", "-d", str(t.get("description") or "")[:250]],
                           capture_output=True, timeout=20, cwd=project)
            created.append(name[:40])
        except Exception:  # noqa: BLE001
            pass
    return created


def _goal_freeze() -> bool:
    try:
        from urirun_connector_work import goal
        return goal.freeze_self_evolution()
    except Exception:  # noqa: BLE001
        return False


def _evolve_reflect(project: str) -> list:
#     """Pętla ewolucji: refleksja nad śladem URI-procesów -> tickety lepszego kodu (journal.evolve)."""
    try:
        from urirun_connector_journal import core as j
        return j.evolve(project).get("created", [])
    except Exception:  # noqa: BLE001
        return []


def _rabbit_hole_react(project: str) -> list[dict]:
#     """AUTONOMIA: spina koincydencje ticketów w królicze nory + uczy mind (świadomość).
#     Deleguje do watchdog.rabbit_hole_reap - jeden punkt reakcji na klaster, idempotentnie."""
    try:
        from urirun_connector_watchdog import core as wd
        return wd.rabbit_hole_reap(project).get("reacted", [])
    except Exception:  # noqa: BLE001
        return []


def _mind_reflect(project: str, actions: list[dict]) -> None:
#     """PO decyzji: zapisz epizody do mind (episode_store), żeby powtarzalne decyzje stały się known-good.
#     Uczenie zamyka pętlę świadomości: reaktywne dziś -> recall->known-good jutro."""
    try:
        from urirun_mind import episode_store
    except Exception:  # noqa: BLE001
        return
    for a in actions:
        try:
            episode_store.record({"intent": a.get("ticket", ""), "flow": [a.get("action", "")],
                                  "result": "planned", "risk": a.get("risk", ""),
                                  "source": "loop-decide"})
        except Exception:  # noqa: BLE001
            continue


def _agent_running(tid: str) -> bool:
#     """Czy żyje agent (claude -p) dla tego ticketu? (żeby nie reapować aktywnej pracy)."""
    import glob
    for c in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            cl = open(c, "rb").read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        if "claude -p" in cl and tid in cl:
            return True
    return False


def _rabbit_hole_members(project: str) -> dict[str, str]:
#     """ŚWIADOMOŚĆ->AUTONOMIA: {ticket_id: topic} dla członków ZIDENTYFIKOWANYCH nor. Reaper/decide
#     ZAMRAŻAJĄ ich (blocked), by nie re-otwierać/re-uruchamiać - koniec mikro-loopa w norze."""
    try:
        from urirun_connector_watchdog import core as wd
        r = wd.rabbit_hole_correlate(project)
        return {tid: h["topic"] for h in r.get("holes", []) for tid in h.get("tickets", [])}
    except Exception:  # noqa: BLE001
        return {}


def _reap_stale_in_progress(project: str) -> list[dict]:
#     """REAPER (self-healing): ticket in_progress bez żywego agenta = utknął. verify:// zielony -> done;
#     inaczej -> open (pętla re-routuje: escalate-human/route-koru/run-agent). Domyka lukę auto-cleaningu."""
    import json
    import subprocess
    pf = _planfile_bin()
    if not pf:
        return []
    try:
        cp = subprocess.run([pf, "ticket", "list", "--status", "in_progress", "--format", "json"],
                            capture_output=True, text=True, timeout=15, cwd=_project(project))
        raw = cp.stdout
        data = json.loads(raw[raw.index("["):raw.rindex("]") + 1]) if "[" in raw else []
    except Exception:  # noqa: BLE001
        return []
    reaped = []
    for t in data:
        tid = t.get("id")
        if not tid or _agent_running(tid):
            continue  # aktywny agent - zostaw
        done = False
        try:
            from urirun_connector_verify import core as vf
            r = vf.ticket_query_check(id=tid, cwd=_project(project))
            done = bool(r.get("all_passed") and r.get("total"))
        except Exception:  # noqa: BLE001
            pass
        if done:
            status, note = "done", "reaper: verify zielony, agent skończył -> done"
        else:
            ready = False  # nie-gotowe (brak zdolności/creds/deps) -> blocked (CZEKA, nie churn)
            try:
                from urirun_connector_continuity import core as gap
                ready = bool(gap.analyze(project, tid).get("ready"))
            except Exception:  # noqa: BLE001
                ready = True  # nie umiem ocenić -> open (retry)
            status = "open" if ready else "blocked"
            note = ("reaper: agent skończył bez ukończenia, gotowe -> open (retry przez pętlę)" if ready
                    else "reaper: nie-gotowe (brak zdolności/creds/prereq) -> blocked, CZEKA (nie churn)")
        if status == "blocked":
            subprocess.run([pf, "ticket", "update", tid, "--note", note],
                           capture_output=True, text=True, timeout=15, cwd=_project(project))
            subprocess.run([pf, "ticket", "block", tid, "-r", note],
                           capture_output=True, text=True, timeout=15, cwd=_project(project))
        else:
            subprocess.run([pf, "ticket", "update", tid, "--status", status, "--note", note],
                           capture_output=True, text=True, timeout=15, cwd=_project(project))
        reaped.append({"ticket": tid, "to": status})
    return reaped


def _reconcile_deps(project: str) -> list[str]:
#     """RECONCILER zależności (level-triggered): odblokuj tickety, których WSZYSTKIE blocked_by są done.
#     To odpowiedź "jak uruchomić 42" - gdy 41 (blocker) -> done, 42 staje się runnable i wraca do open."""
    import json
    import re
    import subprocess
    pf = _planfile_bin()
    if not pf:
        return []
    try:
        cp = subprocess.run([pf, "ticket", "list", "--format", "json"], capture_output=True,
                            text=True, timeout=15, cwd=_project(project))
        raw = cp.stdout
        data = json.loads(raw[raw.index("["):raw.rindex("]") + 1]) if "[" in raw else []
    except Exception:  # noqa: BLE001
        return []
    status = {t.get("id"): t.get("status") for t in data}

    def _fresh_status(dep: str) -> str | None:
        try:
            return _show(project, dep).get("status") or status.get(dep)
        except Exception:  # noqa: BLE001
            return status.get(dep)

    unblocked = []
    for t in data:
        if t.get("status") != "blocked":
            continue
        fresh = _show(project, str(t.get("id") or ""))
        if (fresh.get("status") or t.get("status")) != "blocked":
            continue
        deps = set(t.get("blocked_by") or []) | set(fresh.get("blocked_by") or [])
        notes = list(((t.get("outputs") or {}).get("notes") or []))
        notes += list(((fresh.get("outputs") or {}).get("notes") or []))
        for note in notes:
            deps |= set(re.findall(r"blocked_by\s+([A-Z]+-\d+)", str(note)))
        deps.discard(t.get("id"))
        if deps and all(_fresh_status(d) in ("done", "closed") for d in deps):
            still_blocked = _post_dependency_blockers(project, {**t, **fresh})
            if still_blocked:
                _note_once(project, t["id"],
                           f"reconciler: blokery zależności DONE, ale nadal wymagane: {sorted(still_blocked)}")
                continue
            subprocess.run([pf, "ticket", "update", t["id"], "--status", "open", "--note",
                            f"reconciler: blockery {sorted(deps)} DONE -> runnable (open)"],
                           capture_output=True, text=True, timeout=15, cwd=_project(project))
            unblocked.append(t["id"])
    return unblocked


def _post_dependency_blockers(project: str, ticket: dict) -> list[str]:
#     """Po zdjęciu blocked_by ticket może nadal NIE być runnable (actor:human, mock Signal,
#     brak sprawdzalnego verify). Reconciler nie może wtedy robić open."""
    try:
        from urirun_connector_continuity import core as gap
        ctx = gap._ctx(project)
        probe = dict(ticket)
        probe["status"] = "open"  # symuluj zdjęcie samej blokady zależności
        analysis = gap._analyze(probe, str(ticket.get("id") or ""), ctx)
        return [m for m in (analysis.get("missing") or []) if not str(m).startswith("status ")]
    except Exception:  # noqa: BLE001
        return []


def _note_once(project: str, tid: str, note: str) -> None:
    import subprocess
    pf = _planfile_bin()
    if not pf:
        return
    try:
        t = _show(project, tid)
        notes = " ".join(str(n) for n in ((t.get("outputs") or {}).get("notes") or []))
        if note in notes:
            return
        subprocess.run([pf, "ticket", "update", tid, "--note", note],
                       capture_output=True, text=True, timeout=15, cwd=_project(project))
    except Exception:  # noqa: BLE001
        return


def plan(project: str = "") -> dict[str, Any]:
#     """Co pętla ZROBIŁABY teraz (dry-run, read-only) - plan akcji per ticket, po ocenie safe://."""
    proj = _project(project)
    scan = _gap_scan(proj)
    avail = _available_schemes()  # grounding raz na plan (nie per-ticket)
    actions = []
    for r in scan.get("tickets", []):
        a = _decide(r, proj)
        if not a:
            continue
        adv = _mind_advice(r.get("name") or a["ticket"], avail)  # FALLBACK ŚWIADOMOŚCI
        if adv:
            a["mind"] = adv  # known-good (skill/episode) przed LLM + antywzorce
        if a["action"] == "spawn-capability-ticket":
            sch = _scheme_for(proj, a["ticket"])
            acq = _capability_acquire(sch) if sch else None
            if acq:
                a["capability_acquire"] = acq  # mind.capability_graph: JAK zdobyć brakującą zdolność
        actions.append(a)
    by_risk: dict[str, int] = {}
    for a in actions:
        by_risk[a["risk"]] = by_risk.get(a["risk"], 0) + 1
    return {"actions": actions, "by_risk": by_risk, "systemic": scan.get("systemic", []),
            "total": len(actions)}


def _apply_one(project: str, a: dict, auto_agent: bool) -> dict:
    act = a["action"]
    tid = a["ticket"]
    try:
        if act == "circuit-break":
            from urirun_connector_watchdog import core as wd
            r = wd.loop_command_circuit_break(id=tid, project=project)
        elif act == "escalate-human":
            from urirun_connector_watchdog import core as wd
            r = wd.ticket_command_unstick(id=tid, project=project)
        elif act == "run-agent":  # tu trafia TYLKO safe-auto (safe:// przepuścił)
            if not auto_agent:
                return {"ticket": tid, "action": act, "applied": False, "why": "safe-auto, ale auto_agent=False"}
            r = _run_agent(project, tid)
        elif act == "spawn-capability-ticket":  # SELF-EXTENSION przez TICKET (bezpieczne, auto)
            r = _spawn_capability_ticket(project, tid)
        elif act == "execute-via-twin-human":
            r = _execute_via_twin_human(project, tid)
        else:  # agent-gated / report-missing-criteria / need-capability - bez mutacji
            return {"ticket": tid, "action": act, "applied": False, "why": a.get("reason") or "report-only"}
    except Exception as exc:  # noqa: BLE001
        return {"ticket": tid, "action": act, "applied": False, "error": str(exc)}
    return {"ticket": tid, "action": act, "applied": bool(r.get("ok")), "result": r}


def _planfile_bin() -> str | None:
    import shutil
    b = os.environ.get("URIRUN_PLANFILE_BIN") or shutil.which("planfile")
    if b:
        return b
    from pathlib import Path
    for c in ("~/github/if-uri/venv/bin/planfile", "~/github/semcod/koru/.venv/bin/planfile"):
        if Path(c).expanduser().is_file():
            return str(Path(c).expanduser())
    return None


def _scheme_for(project: str, tid: str) -> str:
    import re
    try:
        from urirun_connector_continuity.core import _ticket
        t = _ticket(project, tid)
    except Exception:  # noqa: BLE001
        return ""
    m = re.search(r"\b([a-z]+)://", (str(t.get("description", "")) + " " + str(t.get("name", ""))).lower())
    if m:
        return m.group(1)
    for lab in (t.get("labels") or []):
        if str(lab).isalpha() and str(lab) not in ("code", "signal", "high", "low"):
            continue
        if str(lab).isalpha():
            return str(lab)
    return ""


def _spawn_capability_ticket(project: str, tid: str) -> dict:
#     """WSZYSTKO PRZEZ TICKETY: utwórz ticket generacji brakującego connectora (idempotentnie).
    # Nowy ticket sam przejdzie cykl (run-agent->verify). Oznacza rodzica blocked_by.
    import subprocess
    pf = _planfile_bin()
    if not pf:
        return {"ok": False, "error": "planfile niedostępny"}
    scheme = _scheme_for(project, tid) or "unknown"
    gen_label, retry_label = f"capgen:{tid}", f"capretry:{tid}"
    have_gen = have_retry = None
    try:  # idempotencja PER-TICKET pary - twórz tylko brakujący
        import json as _json
        cp = subprocess.run([pf, "ticket", "list", "--format", "json"], capture_output=True,
                            text=True, timeout=15, cwd=_project(project))
        raw = cp.stdout
        data = _json.loads(raw[raw.index("["):raw.rindex("]") + 1]) if "[" in raw else []
        for t in data:
            labs = t.get("labels") or []
            if t.get("status") in ("done", "closed"):
                continue
            if gen_label in labs:
                have_gen = t.get("id")
            if retry_label in labs:
                have_retry = t.get("id")
    except Exception:  # noqa: BLE001
        pass
    both_existed = bool(have_gen and have_retry)
    import re

    def _create(name: str, desc: str, *labels: str) -> str:
        cp = subprocess.run([pf, "ticket", "create", name, "-p", "high", "--source", "loop-self-extension",
                             "-d", desc, *sum((["-l", l] for l in labels), [])],
                            capture_output=True, text=True, timeout=20, cwd=_project(project))
        return (re.search(r"[A-Z]+-\d+", cp.stdout) or [None, ""])[1] if (cp.returncode == 0 and cp.stdout) else ""

    def _note(t: str, txt: str) -> None:
        subprocess.run([pf, "ticket", "update", t, "--note", txt], capture_output=True, text=True,
                       timeout=15, cwd=_project(project))

    def _set_blocked_by(t: str, deps: list[str]) -> None:
        try:
            from planfile import Planfile
            cur = Planfile(_project(project)).get_ticket(t)
            have = list(cur.blocked_by or []) if cur else []
            merged = have + [d for d in deps if d and d not in have]
#             Planfile(_project(project)).update_ticket(t, blocked_by=merged)
        except Exception:  # noqa: BLE001
            pass
    try:
        # Ticket A - ZROBIENIE connectora (kod -> run-agent->verify)
        gen_id = have_gen or _create(
            f"Wygeneruj connector {scheme}:// (self-extension dla {tid})",
            f"Auto-wykryte przez loop://: {tid} wymaga {scheme}://. Wygeneruj connector "
            f"(connectorgen). GRANICA: narzędzie zewn. ({scheme}-cli)+konto osobno.",
            "connector-gen", gen_label, "code")
        # Ticket B - PONOWNE uruchomienie zadania z gotowym connectorem
        retry_id = have_retry or _create(
            f"Ponów {tid} z connectorem {scheme}:// (po generacji)",
            f"Gdy {gen_id} gotowy i {scheme}:// działa - wykonaj ponownie zadanie {tid}. blocked_by {gen_id}.",
            retry_label, scheme)
        if not have_retry:
#             _note(retry_id, f"loop: blocked_by {gen_id} (czeka na connector {scheme}://)")
#             _set_blocked_by(retry_id, [gen_id])
            subprocess.run([pf, "ticket", "block", retry_id, "-r", f"blocked_by {gen_id} (czeka na connector {scheme}://)"],
                           capture_output=True, text=True, timeout=15, cwd=_project(project))
        if not both_existed:
            # _note(tid, f"loop: blocked_by {gen_id}->{retry_id} (self-extension...)")
            # _set_blocked_by(tid, [gen_id])
            pass
        # Zaseeduj acceptance_criteria (ZAWSZE, idempotentnie) - bez nich tickety są NIEWYKONYWALNE
        try:
            from urirun_connector_verify import core as vf
            vf.ticket_command_seed(id=gen_id, checks=[
                {"label": f"connector {scheme}:// importowalny",
                 "cmd": f"venv/bin/python -c 'import urirun_connector_{scheme}'"},
                {"label": f"{scheme}:// ma bindings",
                 "cmd": f"venv/bin/python -c \"import urirun_connector_{scheme} as m; assert m.urirun_bindings()['bindings']\""}])
            parent = vf._load().get(tid)
            if parent:
                vf.ticket_command_seed(id=retry_id, checks=parent)  # ponów = kryteria oryginału
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "already": both_existed, "generate": gen_id, "retry": retry_id,
            "scheme": scheme, "parent": tid, "chain": f"{tid} blocked_by {gen_id} (zrób) -> {retry_id} (ponów)"}


def _run_agent(project: str, tid: str) -> dict:
    # Wykonaj ticket agentem. Preferuje host work_runs (legibilny); fallback: agent:// wprost.
    try:
        from urirun.host import agent_admin
        return agent_admin.run_ticket(project, tid)
    except Exception:  # noqa: BLE001
        from urirun_connector_agents import core as ag
        return ag.task_run(prompt=f"Execute ticket {tid} in this repo. Minimal, tested change.",
                           agent="claude", cwd=project)


def _ticket_actor(t: dict) -> str:
    # Zadeklarowany AKTOR ticketu (actor: label). Priorytet: human > koru > ci > agent.
    actors = [str(l).split(":", 1)[1] for l in (t.get("labels") or []) if str(l).startswith("actor:")]
    for a in ("human", "koru", "ci"):
        if a in actors:
            return a
    return "agent"


def _open_ready(project: str) -> list[dict]:
    import json
    import subprocess
    pf = _planfile_bin()
    if not pf:
        return []
    out = []
    for st in ("open", "ready"):
        try:
            cp = subprocess.run([pf, "ticket", "list", "--status", st, "--format", "json"],
                                capture_output=True, text=True, timeout=15, cwd=_project(project))
            raw = cp.stdout
            out += json.loads(raw[raw.index("["):raw.rindex("]") + 1]) if "[" in raw else []
        except Exception:  # noqa: BLE001
            pass
    return out


def assign(project: str = "", claim: bool = False) -> dict[str, Any]:
    # MULTI-AGENT + MULTI-ACTOR: routuj RUNNABLE tickety do właściwego aktora -
    # actor:human -> human:// (eskalacja do operatora); actor:koru -> koru; agent -> match po node+capability.
    # claim=True -> planfile claim (wzajemne wykluczenie dla agentów).
    proj = _project(project)
    agents = _agents()
    out = []
    for t in _open_ready(proj):
        tid = t.get("id")
        actor = _ticket_actor(t)
        if actor == "human":
            out.append({"ticket": tid, "actor": "human", "route": f"human://operator/decision/{tid}",
                        "agent": None, "reason": "krok wymaga człowieka (np. token/link/telefon)"})
        elif actor == "koru":
            out.append({"ticket": tid, "actor": "koru", "route": "koru://queue", "agent": "koru",
                        "reason": "krok dla koru (publikacja/deploy)"})
        elif actor == "ci":
            out.append({"ticket": tid, "actor": "ci", "route": "ci://pipeline", "agent": "ci"})
        else:
            need = _ticket_need(t)
            agent = _match(need, agents)
            rec = {"ticket": tid, "actor": "agent", "need": need, "agent": agent,
                   "route": f"agent://{agent}" if agent else None, "claimed": False}
            if agent and claim:
                rec["claimed"] = _claim(proj, tid, agent)
            out.append(rec)
    return {"agents": agents, "assignments": out,
            "human_steps": [x["ticket"] for x in out if x["actor"] == "human"],
            "koru_steps": [x["ticket"] for x in out if x["actor"] == "koru"],
            "unassigned": [x["ticket"] for x in out if x["actor"] == "agent" and not x["agent"]]}


def _show(project: str, tid: str) -> dict:
    try:
        from urirun_connector_continuity.core import _ticket
        return _ticket(project, tid)
    except Exception:  # noqa: BLE001
        return {"id": tid}


def _claim(project: str, tid: str, agent: str) -> bool:
    import subprocess
    pf = _planfile_bin()
    if not pf:
        return False
    try:
        cp = subprocess.run([pf, "ticket", "claim", tid], capture_output=True, text=True,
                            timeout=15, cwd=_project(project))
        return cp.returncode == 0
    except Exception:  # noqa: BLE001
        return False


@conn.handler("agents/query/assign", isolated=False,
              meta={"label": "Multi-agent: przypisz runnable tickety do agentów po node+capability (+claim)"})
def agents_query_assign(project: str = "", claim: bool = False) -> dict[str, Any]:
    try:
        return _ok(action="loop-assign", **assign(project, bool(claim)))
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc), "loop-assign")


@conn.handler("policy/query/plan", isolated=False,
              meta={"label": "Plan pętli korekcyjnej (dry-run): jaka akcja dla którego ticketu"})
def policy_query_plan(project: str = "") -> dict[str, Any]:
    try:
        return _ok(action="loop-plan", **plan(project))
    except Exception as exc:  # noqa: BLE001
        return _fail(str(exc), "loop-plan")


def _execute_via_twin_human(project: str, tid: str) -> dict:
    # Delegacja do urirun-twin-human dla zadań kvm/lenovo (realne komendy URI na węzeł, logowane do queue.log).
    # Dla ticketów signal/kvm: deliver_signal (signal-gui-kvm) lub act_as_human.
    # Wpisy kvm://host/... widoczne w panelu "Na żywo - koru (realne komendy URI)".
    import subprocess, time, json as _json
    from pathlib import Path as _Path

    pf = _planfile_bin()
    if not pf:
        return {"ok": False, "error": "planfile niedostępny"}

    try:
        cp = subprocess.run([pf, "ticket", "show", tid, "--format", "json"], capture_output=True,
                            text=True, timeout=10, cwd=_project(project))
        t = _json.loads(cp.stdout)
    except Exception:
        t = {"id": tid, "name": "", "description": "", "labels": []}

    name = (t.get("name") or "").lower()
    labels = [str(x).lower() for x in (t.get("labels") or [])]

    log_path = _Path(_project(project)) / ".planfile" / ".koru" / "queue.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log_uri(uri: str, payload: dict | None = None, ok: bool = True):
        ts = time.strftime("%H:%M:%S")
        pl = f" {payload}" if payload else ""
        line = f"[{ts}] koru ▸ URI: {uri}{pl} -> {'ok' if ok else 'fail'}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)

    try:
        from urirun_connector_work.signal_kvm import (
            TWIN_HUMAN_ACTOR,
            is_signal_ticket,
            payload_from_ticket,
            resolve_node,
        )
        # deliver path for signal on lenovo
        if is_signal_ticket(t):
            from urirun_connector_work import goal as wg
            rec, msg = payload_from_ticket(t)
            node = resolve_node(t)
            res = wg.deliver_signal(recipient=rec, message=msg, approved=True, ticket=tid, node=node)

            # Sukces tylko gdy deliver_signal potwierdzi wysyłkę (postcondition + efekt w UI)
            ok = bool(res.get("message_sent"))
            inner_verified = bool(res.get("verified"))
            v = res.get("_verification") or {}

            log_status = "verified" if ok else f"NOT-VERIFIED (verified={inner_verified}, verdict={v.get('verdict')}, violations={v.get('violations')})"
            _log_uri("signal://message/send", {"recipient": rec, "verified": inner_verified, "status": log_status}, ok)

            if ok:
                # complete ticket via twin with actor/reason so history has it
                try:
                    from urirun.host import planfile_adapter as pa
                    pa.complete_ticket(project, tid,
                                       note=f"executed via urirun-twin-human + real kvm on lenovo node (verified={inner_verified})",
                                       reason="real KVM click+type+send via twin-human delegation + postcondition verified",
                                       actor=TWIN_HUMAN_ACTOR)
                except Exception:
                    import subprocess
                    subprocess.run([pf, "ticket", "done", tid], cwd=_project(project), capture_output=True)
            else:
                # Nie zamykaj jako sukces jeśli weryfikacja nie przeszła
                _log_uri(f"ticket://{tid}/verify-failed", {"reason": "tekst nie pojawił się w Signal po wysyłce"}, False)

            return {"ok": ok, "result": res, "via": "twin+deliver+kvm", "actor": TWIN_HUMAN_ACTOR, "verified": inner_verified}
        else:
            from urirun_twin_human.core import act_as_human
            res = act_as_human("tom", goal=f"execute {tid} {name}", context={"ticket": t}, ticket=tid)
            _log_uri("human://tom/kvm/execute", {"ticket": tid}, bool(res.get("ok", True)))
            return {"ok": bool(res.get("ok", True)), "result": res, "via": "twin-human"}
    except Exception as exc:
        _log_uri("twin-human error", {"err": str(exc)}, False)
        return {"ok": False, "error": str(exc)}


@conn.handler("cycle/command/run", isolated=True,
              meta={"label": "Jeden cykl pętli: zastosuj SAFE akcje auto; run-agent za bramką auto_agent"})
def cycle_command_run(project: str = "", apply: bool = False, auto_agent: bool = False) -> dict[str, Any]:
    # OBSERWUJ->ZDECYDUJ->DZIAŁAJ w jednym przebiegu. apply=False -> tylko plan (bezpieczne).
    proj = _project(project)
    reaped = _reap_stale_in_progress(proj) if apply else []  # najpierw: posprzątaj utknięte in_progress
    unblocked = _reconcile_deps(proj) if apply else []        # potem: odblokuj co ma blockery done
    purged = _triage_cleanup(proj) if apply else []           # AUTONOMIA: usuń stale (retry-of-done/nora/diagnozy) - bez człowieka
    holes = _rabbit_hole_react(proj) if apply else []
    _frozen = _goal_freeze() if apply else False  # GOAL-MODE: nie generuj refaktor/self-evolution/backlog w trakcie celu
    evolved = _evolve_reflect(proj) if (apply and not _frozen) else []         # SAMODOSKONALENIE: refleksja nad śladem URI -> tickety ewolucji
    refilled = _backlog_refill(proj) if (apply and not _frozen) else []        # GENERATOR: koru idle -> nxdo (semcod) plan -> tickety (nigdy bezczynny)
    p = plan(proj)
    if apply:
        # _mind_reflect(proj, p["actions"])  # UCZ SIĘ...
        pass
    if not apply:
        return _ok(action="loop-cycle", applied=[], dry_run=True, reconciled=[], reaped=[], **p)
    applied = [_apply_one(proj, a, auto_agent) for a in p["actions"]]
    return _ok(action="loop-cycle", applied=applied, dry_run=False, reconciled=unblocked, reaped=reaped,
               purged=purged, did=sum(1 for x in applied if x.get("applied")), by_risk=p["by_risk"],
               systemic=p["systemic"], total=p["total"])


def _triage_cleanup(project: str) -> list[str]:
    # AUTONOMIA (bez człowieka): usuń jednoznacznie stale tickety (retry-of-done, nory-rozwiązane, kaskady diagnoz).
    # Deleguje do continuity.triage(apply_deletes=True) - tylko high-confidence.
    try:
        from urirun_connector_continuity import core as gap
        return gap.triage(project, apply_deletes=True).get("auto_deleted", [])
    except Exception:  # noqa: BLE001
        return []


def urirun_bindings() -> dict[str, Any]:
    return conn.bindings()


def connector_manifest() -> dict[str, Any]:
    return urirun.load_manifest(__package__) or {"id": CONNECTOR_ID}


def main(argv: list[str] | None = None) -> int:
    return conn.cli(argv, manifest_prose=urirun.load_manifest(__package__))


if __name__ == "__main__":
    raise SystemExit(main())
