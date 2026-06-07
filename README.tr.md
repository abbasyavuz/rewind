# Rewind

[English](README.md) | Türkçe

**Yapay zeka ajanları için bir uçuş kaydedici ve zaman yolculuğu hata ayıklayıcısı.**

Bir ajan production'da yanlış davrandığında çoğu zaman yeniden üretemezsiniz; model deterministik
değildir ve iz kaybolmuştur. Rewind, bir ajan çalışmasının tüm deterministik olmayan sınırlarını
(model çağrıları, araç sonuçları, retrieval, saat, RNG, HTTP) **imzalı, içerik-adresli,
çevrimdışı doğrulanabilir bir `.rewind` artifact'i** içine kaydeder; ardından bunu
**deterministik olarak replay** etmenizi, **zaman akışını geri sarmanızı** ve şu karşı-olgusal
soruyu sormanızı sağlar: *"Bu tek sınır X döndürseydi ne olurdu?"* Bir şeyi değiştirin, akışın
nasıl ayrıştığını görün.

Rewind, **framework'ün alt katmanına** (`httpx` transport seviyesine) bağlanır; bu yüzden `httpx`
üzerinden çalışan her istemci **kod değişikliği olmadan** kaydedilir. OpenAI ve Anthropic SDK'ları
ile uçtan uca doğrulandı; bu SDK'lara / `httpx`'e delegasyon yapan framework'ler (LangGraph,
CrewAI, vb.) de aynı kanca üzerinden yakalanır. Bu kapsama güveniyoruz ama henüz ayrı bir örnek
olarak yayınlamıyoruz.

> ### Hendek: kanıtlanabilir kısım
> **Self-hosted / OSS modellerde** sampler bizim kontrolümüzde olduğu için bir sınır **kanonik
> bit-düzeyinde** yeniden çalıştırılabilir; volatile `id` / `created` / `usage` / `logprobs`
> alanları atılır (noise floor = 0). Böylece bir fork'un ayrışması örnekleme gürültüsünden değil,
> **senin yaptığın değişiklikten** kaynaklanır. **Kapalı API'lerde** (Claude / GPT / Gemini)
> Rewind daha dürüst davranır: **ilk sınıf `INDETERMINATE` ile en iyi çaba divergence triage**
> sunar; adli bir sertifika iddiasında bulunmaz. Nedenini ölçtük; bkz. [Evidence](#evidence).

Apache-2.0 · open-core, OpenTelemetry tarzı · gerçek bir hosted model (herhangi bir OpenAI-uyumlu
sağlayıcı) ve yerel bir OSS model (Ollama) üzerinde doğrulandı. Sağlayıcıyı ve modeli sen seçersin —
Rewind hiçbir varsayılan model id'siyle gelmez.

---

## Çalışırken görün

Bir destek ajanı kırılgan bir aracı seçti, timeout oldu ve müşteriye "şu an bunu kontrol
edemiyorum" dedi. Rewind olayı çevrimdışı yeniden üretti; sonra **tek bir** model kararını
değiştirip *ne olurdu* sorusunu sorduk ve düzeltmeyi git-benzeri, bağımsız doğrulanabilir bir diff
olarak kanıtladık:

```console
$ rewind diff runs/support.rewind runs/support-fixed.rewind --verify \
    --pubkey-a runs/support.rewind/run-key.pub --pubkey-b runs/support-fixed.rewind/run-key.pub
trust: runs/support.rewind VERIFIED ✓   runs/support-fixed.rewind VERIFIED ✓
prefix: 1 identical · diverged at seq 1 · forked at seq 1 · frontier: +2 −2

@@ seq 1 (c718f8c10af5) — same request, response changed @@
  {
-   "tool": "legacy_billing"
+   "tool": "billing_v2"
  }

@@ frontier (unaligned — the path not taken vs the counterfactual branch) @@
- seq 2 POST -> {"out":"ERROR: upstream timeout"}
- seq 3 POST -> {"answer":"Sorry, I can't check that right now."}
+ seq 2 POST -> {"out":"refund $42.00 sent on 2026-05-28"}
+ seq 3 POST -> {"answer":"Your $42.00 refund was sent on 2026-05-28."}
```

Deterministik prefix bayt düzeyinde aynıdır; sadece pertürbe edilen dal ayrışır. `exit 1`, CI'ın
"bu düzeltme akışı değiştirdi" diye assertion koymasını sağlar.

## Bitwise hendek, otomatik

Self-host ettiğiniz bir modelde Rewind, replay/fork akışını **tasarım gereği yeniden üretilebilir**
hale getirir; birinci sınıf `Deterministic` profili sampler'ı sabitler:

```console
$ python examples/deterministic_oss.py        # Ollama llama3.2:3b, GPU yok
det.verify_replay → seq 0: bitwise ✓   seq 1: bitwise ✓      # kayıt bit-for-bit replay edilir
fork ×2 (inference=det) → frontier canon identical → counterfactual REPRODUCIBLE ✓
```

Bunun şans değil hendek olmasının nedeni: aynı model üzerinde temsili bir A/A koşusu (aynı girdi,
N=10, temp=1.0; tam sayılar koşudan koşuya değişir):

| Ayar | Noise floor | |
|---|---|---|
| self-hosted, **seed bizim tarafımızdan sabitli** | **0.00** | bitwise yeniden üretilebilir -> divergence %100 atfedilebilir |
| self-hosted, seed yok | ~0.6 | determinismin **bizim kontrolümüz** olduğunu gösterir |
| kapalı hosted API | kontrol edilemez | kolay promptlarda stabil; [Spike-1](#evidence) **INCONCLUSIVE** döndü (temp=1.0'da sınır-vaka çıkmadı) — karar sınırı yakınında noise floor burada ölçülemez ve zaten pin'lenemez |

## Hızlı başlangıç

```bash
make dev      # Rust CLI + PyO3 native module + Python SDK'yı venv içine kurar
make test     # cargo test + pytest
```

`import rewind`, ayrı bir maturin crate'i olan native module'e (`rewind_native`) ihtiyaç duyar;
yalnızca `pip install` **yeterli değildir**. Bu yüzden `make dev` kullanın (veya manuel olarak):

```bash
cargo build --release                                  # `rewind` CLI
python3 -m venv python/rewind/.venv && . python/rewind/.venv/bin/activate
pip install -U maturin && pip install -e "python/rewind[dev,examples]"
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin develop --release -m crates/rewind-py/Cargo.toml
```

```python
import rewind

with rewind.record("incident", out_dir="./incident.rewind"):
    run_my_agent()                      # OpenAI/Anthropic/LangGraph/... framework altından yakalanır

# Deterministic replay — ağ yok, anahtar yok: her sınır kayıttan servis edilir.
with rewind.replay("./incident.rewind") as rep:
    run_my_agent()
    print(rep.report())                 # {recorded, served, unused}; divergence/ambiguity -> FAIL LOUD

# Counterfactual fork — bir sınırı değiştir, akışın nasıl ayrıştığını gör.
with rewind.fork("./incident.rewind", at=1, swap_response=(200, b'{"tool":"billing_v2"}'),
                 inference=rewind.Deterministic(seed=42),   # ayrışan dal OSS modeli sabit seed ile yeniden çalıştırır
                 record_to="./fixed.rewind"):
    run_my_agent()
```

## Zaman yolculuğu hata ayıklayıcısı (Rust CLI — çevrimdışı, Python gerektirmez)

```bash
rewind log    incident.rewind                # timeline: sınır başına bir satır (`less -R` ile gez)
rewind show   incident.rewind 3              # tek bir sınırın request + response'u (seq veya causal-id)
rewind diff   a.rewind b.rewind --verify     # prefix · divergence · frontier; kurcalanmış tarafı reddeder
rewind verify incident.rewind --pubkey k.pub # herkes integrity + signature'ı çevrimdışı doğrulayabilir
rewind log    x.rewind --json | jq …         # log, inspect, verify için --json yüzeyi var
```

`rewind` ikilisi kendi kendine yeterlidir: BLAKE3 hash chain'i, Merkle root'u ve Ed25519 imzasını
bize güvenmeden yeniden türetir; yani `.rewind` bağımsız doğrulanabilir ve kurcalamaya dayanıklıdır.

## Örnekler

Her biri `.env`'de seçtiğin sağlayıcı ve modele karşı **gerçek** bir ajanı (herhangi bir OpenAI-uyumlu
hosted API veya Ollama üzerinden yerel bir OSS model) sıfır SDK değişikliğiyle yakalar. **Önce `record`
çalıştır** (key + model gerekir); `replay`/`fork` sonra *senin* kaydını çevrimdışı tekrar üretir. Bkz. [`examples/`](examples/):

- [`openrouter_agent.py`](examples/openrouter_agent.py) — `record` · `replay` · `fork` (hazır veya `--live` frontier).
- [`tooluse_agent.py`](examples/tooluse_agent.py) — çok adımlı tool-use ajanı; reasoning + tool izi çevrimdışı yakalanır ve tekrar üretilir.
- [`deterministic_oss.py`](examples/deterministic_oss.py) — bitwise katman: `verify_replay` + yeniden üretilebilir fork.

> Repodaki hazır `runs/support.rewind`, yukarıdaki **Rust `rewind diff` vitrin demosu** içindir — Python
> örnek ajanlarıyla eşleşmez, dolayısıyla orada `replay`/`fork`'tan önce `record` çalıştır.

## Evidence

Hendeği sadece iddia etmiyoruz; eşikleri önceden tanımlayıp çalıştırılabilir harness'lerle ölçtük:

- **[`spikes/spike1_envelope.py`](spikes/spike1_envelope.py)** (divergence-envelope) — kapalı API'de
  tek örnek attribution, model kendinden eminse kolay; **karar sınırına yakınsa kötü/ölçülemez**
  (identifiability wall). Başlıktaki hendek bu yüzden kapalı API adli analizi değil, OSS-bitwise.
- **[`spikes/spike_oss_bitwise.py`](spikes/spike_oss_bitwise.py)** (bitwise-OSS replay) — self-hosted
  modelde noise floor 0 çünkü seed bizim elimizde; dolayısıyla fork divergence'ı kanıtlanabilir
  biçimde edit kaynaklıdır.

## Nasıl çalışır

```
senin ajanin ──(httpx transport hook)──► capture ──► rewind-core (Rust): BLAKE3 CID · hash-chained
                                           │           HLC log · Merkle · Ed25519 ─► signed .rewind
   replay / fork  ◄── causal-id match (blake3(parent ‖ request)) · divergence/ambiguity durumunda FAIL LOUD
   rewind CLI     ◄── log · show · diff · verify   (statik binary, çevrimdışı, Python yok)
```

Causal boundary id'leri lineage + request içeriği üzerine kurulur (saat kullanılmaz), bu yüzden
replay sırasında tekrar üretilebilir; parent her adımda ilerler, böylece sıralı tekrarlar ayırt
edilir ve sadece gerçek concurrent çakışmalar reddedilir.

## Durum

**v0 ve çekirdek döngü uçtan uca çalışıyor; gerçek modeller üzerinde doğrulandı.** `cargo test` +
`pytest` yeşil; `record → replay → fork → debugger CLI` akışı çevrimdışı ve çapraz araçlarla
doğrulanabilir.

Dürüst kapsam: kapalı API triage'ı henüz provisional (tek bir hosted model üzerinde pilot yapıldı;
bağlayıcı Claude ölçümü beklemede). Yerel bitwise katman *canonical*-bitwise + imzalıdır; production batching altındaki tam
**raw-byte** batch-invariance GPU/vLLM katmanıdır. Streaming destekleniyor; gateway/Bedrock ve
MCP-over-stdio interceptor'ları fast-follow. Koddaki `# TODO(phase-N)` işaretleri teknik plana
bağlanır.

## Yol haritası

- **Phase 0 (mevcut):** imzalı artifact motoru, deterministic replay, counterfactual fork ve
  çevrimdışı Rust debugger CLI'ı küçük ama dürüst bir v0 olarak sunmak. Bugün çalışan kod bu.
- **Phase 1 (mevcut `# TODO(phase-1)` işaretleri):** yeni yüzeyler tamamlanmış gibi davranmadan
  doğruluğu ve capture coverage'ı sıkılaştırmak. Koddaki altı canlı işaret burada toplanıyor:
  request canonicalization, redaction'ı synchronous hot path dışına taşımak, planlanmış
  nondeterminism kaynakları (`time` / RNG / `uuid`) için gerçek shim'ler ve bugünkü line-level JSON
  görünümünden daha iyi semantic diff.
- **Phase 2 (Phase 1 tabanı oturduktan sonra):** capture surface'leri ve production ergonomisini
  genişletmek: non-`httpx` interceptor'lar (gateway/Bedrock/Vertex/MCP-benzeri sınırlar), daha zengin
  debugger UX'i ve raw-byte batch-invariance için GPU/vLLM katmanı.

Kural basit: Rewind bir boundary veya determinism özelliğini ancak verifier ve testler onu zaten
kanıtlıyorsa iddia eder. Geri kalan her şey açıkça phase'lenir.

## Repo yerleşimi

```
crates/    rewind-core (.rewind motoru) · rewind-cli (verify · log · show · diff)
python/    capture / replay / fork / Deterministic SDK
examples/  gerçek ajanlar (OpenRouter + Ollama)
spikes/    ölçüm harness'leri (envelope + bitwise-OSS)
```

## Lisans

[Apache-2.0](LICENSE).
