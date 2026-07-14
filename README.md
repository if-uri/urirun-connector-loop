# urirun-connector-loop

`loop://` — zamknięta pętla korekcyjna. Czyta `gap://scan` + `watch://detect`, mapuje stan
ticketu na jedną poprawną akcję i (apply=True) ją stosuje.

### Nowe w 2026-07 (twin-human integration)

Dla ticketów z labelami `kvm`, `lenovo`, `signal-gui` (lub tekstem "na lenovo") pętla automatycznie
decyduje o akcji `execute-via-twin-human`.

- Deleguje wykonanie do `urirun-twin-human` (KVM clicks, `deliver_signal` po kanale `signal-gui-kvm`).
- Realne komendy (`kvm://laptop/...`) są logowane do `.planfile/.koru/queue.log` i widoczne w panelu "Na żywo".
- Uruchomienie: `urirun start` lub `make -f app/Makefile koru-cycle`.

SAFE auto (circuit-break dead-loop, escalate needs_input→human://), RISKY za bramką (run-agent), REPORT (brak kryteriów).
Odpalany cyklicznie (cron) czyni luki źródłem autonomicznych reakcji.
