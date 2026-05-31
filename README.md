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

> Not: v0 iskeleti. `rewind-core` primitive'leri (CID/HLC/log/manifest/attest/verify) gerçek
> ama uçtan-uca capture→replay→fork hattı Hafta 2-8'de inşa edilir. `// TODO(phase-N)` işaretleri
> teknik plandaki fazlara bağlanır.

## Lisans

[Apache-2.0](LICENSE).
