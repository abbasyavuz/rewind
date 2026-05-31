# Rewind

**AI agent'ları için uçuş kayıt cihazı + time-travel debugger.**

Bir agent'ın her non-deterministik sınırını (model sampling, tool çıktıları, retrieval, clock,
RNG, HTTP) framework-altında yakalar; imzalı, content-addressed, Merkle-sıralı bir `.rewind`
artefaktına yazar (herhangi bir üçüncü taraf **offline** doğrular); ve bir mühendisin run'ı
**deterministik replay** edip zamanda gezinmesini ve **counterfactual fork** etmesini sağlar.

> **Headline moat — kanıtlanabilir olan:** self-hosted/OSS modeller için **bitwise-exact replay**
> + en-bolt-on-edilemez **imzalı doğrulanabilir artefakt**. Closed API'ler (Claude/GPT/Gemini)
> için dürüst **best-effort divergence triage** — kalibre, birinci-sınıf `INDETERMINATE` etiketli,
> **forensic sertifika olarak DEĞİL.**

Apache-2.0. OpenTelemetry tarzı open-core.

---

## ⚠️ Statü: v0 iskeleti (pre-build)

Bu repo **Hafta-0/Hafta-1 iskeletidir.** Henüz çalışan bir ürün değildir. Üç **existential risk**
Hafta-1'de üç paralel **kill-spike** ile doğrulanır/öldürülür. Kod yazmadan önceki ilk iş —
`spec/pivot-thresholds.md`'deki numerik pivot eşiklerini ratify etmek — bu iskeletin merkezindedir.

Tam gerekçe ve yol haritası: [`docs/rewind-technical-plan.md`](docs/rewind-technical-plan.md).

## Repo düzeni

```
.
├── docs/                       # Fikir + finalize edilmiş teknik plan
│   ├── rewind.md               #   fikir özeti (problem, moat, para, riskler)
│   └── rewind-technical-plan.md#   ⭐ tam mimari + 13 haftalık yol haritası + 3 existential risk
├── spec/                       # Format spec + önceden-kayıtlı kararlar
│   ├── pivot-thresholds.md     #   ⭐ HAFTA-0: Spike-1 X/Y/Z pivot eşikleri (ratify edilecek)
│   ├── rewind-format-v0.1-DRAFT.md  # .rewind artefakt formatı (DONDURULMAMIŞ)
│   └── spikes/                 #   3 Hafta-1 kill-spike planı
├── crates/                     # Rust: .rewind artefakt motoru + CLI
│   ├── rewind-core/            #   BLAKE3 CID, hash-chained HLC log, dCBOR manifest, Ed25519, verify
│   └── rewind-cli/             #   `rewind verify | inspect` (offline, statik binary)
└── python/rewind/              # Python: capture SDK (httpx hook), context, guard, commitment
```

## Üç existential risk (teknik plan §0)

1. **Closed-API envelope kanıtlanabilirliği** — bir *identifiability* problemi (örneklem değil).
2. **Concurrent/multi-agent deterministik replay** — sessiz/kendinden-emin-yanlış başarısızlık.
3. **Ticari talep** — kill-spike ile LOI'ye gate'lenir.

## Hızlı başlangıç (geliştirici)

```bash
# Rust core + CLI (offline verifier)
cargo build --release
./target/release/rewind --help

# Python capture SDK (editable install)
cd python/rewind && pip install -e ".[dev]"
```

```python
import rewind, httpx
with rewind.record("incident", out_dir="./incident.rewind"):
    run_my_agent()                       # boundaries captured below the framework

# Deterministic replay (no network): each boundary served from the recording.
with rewind.replay("./incident.rewind") as rep:
    run_my_agent()
    print(rep.report())                  # {recorded, served, unused}; diverge -> FAIL LOUD

# Counterfactual fork: "what if boundary 0 had returned X?" — rewind, change one
# thing, watch the trajectory diverge. The deterministic prefix is identical.
with rewind.fork("./incident.rewind", at=0, swap_response=(200, b'{"tool":"billing_db"}'),
                 on_frontier=lambda req, body: httpx.Response(200, content=b'{"out":"fixed"}')) as fk:
    run_my_agent()
    print(fk.report())                   # {forked, fork_seq, frontier_hits, ...}
```

### Time-travel debugger (Rust CLI — offline, no Python)

```bash
rewind log    incident.rewind                 # timeline: one row per boundary (pipe to `less -R`)
rewind show   incident.rewind 3               # one boundary's request+response (by seq or causal-id)
rewind diff   incident.rewind fixed.rewind    # prefix · divergence · frontier  (exit 1 if diverged)
rewind diff   incident.rewind fixed.rewind --verify   # refuses to diff a tampered side
rewind log    x.rewind --json | jq …          # every command has a --json machine surface
```

`fork(..., record_to="fixed.rewind")` writes the counterfactual to a second signed artifact, so
`rewind diff` shows — as a git-like, independently verifiable diff — that one changed boundary
turned the failure into the fix.

> Not: v0. **Capture + deterministik replay + counterfactual fork + time-travel debugger CLI çalışıyor.**
> İnteraktif TUI v0.5 (teknik plan; v0'da `rewind log | less`). `// TODO(phase-N)` işaretleri fazlara bağlanır.

## Lisans

[Apache-2.0](LICENSE).
