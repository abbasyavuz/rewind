//! Time-travel debugger commands: `log`, `show`, `diff`.
//!
//! All three share ONE boundary decoder (`decode`) over the single existing loader
//! (`rewind_core::load_log`) — there is no second CBOR/object-reading path. The
//! decoder reads `objects/b3-<raw_cid>.bin`, parses the framed JSON
//! `{request:{method,url,body}, response:{status,body}}`, and degrades gracefully
//! (no panic) for opaque/gateway/binary blobs.

use anyhow::{bail, Context, Result};
use rewind_core::{load_log, verify_artifact, Cid, EventRecord, Manifest};
use serde_json::Value;
use std::path::Path;

// ---------- shared decoder ----------

pub struct Boundary {
    pub raw_ok: bool,
    pub decodable: bool,
    pub method: Option<String>,
    pub url: Option<String>,
    pub req_body: Option<String>,
    pub status: Option<i64>,
    pub resp_body: Option<String>,
    pub raw_bytes: Vec<u8>,
}

pub fn cbid(rec: &EventRecord) -> String {
    rec.causal_boundary_id.to_hex()
}
pub fn kind_str(rec: &EventRecord) -> String {
    format!("{:?}", rec.kind)
}
pub fn surface_str(rec: &EventRecord) -> String {
    format!("{:?}", rec.capture_surface)
}

pub fn decode(objects: &Path, rec: &EventRecord) -> Boundary {
    let raw_bytes = std::fs::read(objects.join(rec.raw_cid.object_filename())).unwrap_or_default();
    let raw_ok = !raw_bytes.is_empty() && Cid::of(&raw_bytes) == rec.raw_cid;
    let (mut decodable, mut method, mut url, mut req_body, mut status, mut resp_body) =
        (false, None, None, None, None, None);
    if let Ok(v) = serde_json::from_slice::<Value>(&raw_bytes) {
        if let (Some(req), Some(resp)) = (v.get("request"), v.get("response")) {
            method = req.get("method").and_then(Value::as_str).map(String::from);
            url = req.get("url").and_then(Value::as_str).map(String::from);
            req_body = req.get("body").and_then(Value::as_str).map(String::from);
            status = resp.get("status").and_then(Value::as_i64);
            resp_body = resp.get("body").and_then(Value::as_str).map(String::from);
            decodable = true;
        }
    }
    Boundary { raw_ok, decodable, method, url, req_body, status, resp_body, raw_bytes }
}

fn short(s: &str, n: usize) -> String {
    let one_line: String = s.split_whitespace().collect::<Vec<_>>().join(" ");
    if one_line.chars().count() > n {
        format!("{}…", one_line.chars().take(n - 1).collect::<String>())
    } else {
        one_line
    }
}

fn pretty_json(s: &str) -> String {
    serde_json::from_str::<Value>(s)
        .ok()
        .and_then(|v| serde_json::to_string_pretty(&v).ok())
        .unwrap_or_else(|| s.to_string())
}

fn read_manifest(dir: &Path) -> Option<Manifest> {
    std::fs::read(dir.join("manifest.cbor"))
        .ok()
        .and_then(|b| Manifest::from_cbor(&b).ok())
}

// ---------- rewind log ----------

#[allow(clippy::too_many_arguments)]
pub fn cmd_log(
    dir: &Path,
    json: bool,
    oneline: bool,
    kind: Option<String>,
    surface: Option<String>,
    from: Option<u64>,
    to: Option<u64>,
    limit: Option<usize>,
) -> Result<()> {
    let records = load_log(dir).context("loading event log")?;
    let objects = dir.join("objects");

    let pass = |r: &EventRecord| {
        kind.as_ref().is_none_or(|k| kind_str(r).eq_ignore_ascii_case(k))
            && surface.as_ref().is_none_or(|s| surface_str(r).eq_ignore_ascii_case(s))
            && from.is_none_or(|f| r.seq >= f)
            && to.is_none_or(|t| r.seq <= t)
    };

    if !json {
        if let Ok(report) = verify_artifact(dir, None) {
            let mark = |b: bool| if b { "✓" } else { "✗" };
            println!(
                "# {}  run={}  events={}  chain {} merkle {}",
                dir.display(),
                report.run_id,
                report.event_count,
                mark(report.chain_ok),
                mark(report.merkle_ok),
            );
        }
        if !oneline {
            println!("{:>4}  {:<12}  {:<11}  {:<8}  {:<2}  {:<4}  summary", "seq", "cbid", "kind", "surface", "ok", "stat");
        }
    }

    let mut shown = 0usize;
    for rec in records.iter().filter(|r| pass(r)) {
        if limit.is_some_and(|n| shown >= n) {
            break;
        }
        shown += 1;
        let b = decode(&objects, rec);
        let summary = b.resp_body.as_deref().map(|s| short(s, 60)).unwrap_or_default();
        let stat = b.status.map(|s| s.to_string()).unwrap_or_else(|| "-".into());
        if json {
            let v = serde_json::json!({
                "seq": rec.seq,
                "cbid": cbid(rec),
                "kind": kind_str(rec),
                "surface": surface_str(rec),
                "raw_ok": b.raw_ok,
                "method": b.method,
                "url": b.url,
                "status": b.status,
                "summary": b.resp_body.as_deref().map(|s| short(s, 120)),
                "meta": rec.meta,
            });
            println!("{v}");
        } else if oneline {
            println!("{:>4}  {}  {:<11}  {}  {}", rec.seq, &cbid(rec)[..12], kind_str(rec), stat, summary);
        } else {
            println!(
                "{:>4}  {:<12}  {:<11}  {:<8}  {:<2}  {:<4}  {}",
                rec.seq,
                &cbid(rec)[..12],
                kind_str(rec),
                surface_str(rec),
                if b.raw_ok { "✓" } else { "✗" },
                stat,
                summary,
            );
        }
    }
    Ok(())
}

// ---------- rewind show ----------

fn resolve<'a>(records: &'a [EventRecord], selector: &str) -> Result<&'a EventRecord> {
    let selector = selector.trim();
    if selector.is_empty() {
        bail!("empty boundary selector — pass a seq or a causal-id prefix");
    }
    if let Ok(seq) = selector.parse::<u64>() {
        return records
            .iter()
            .find(|r| r.seq == seq)
            .with_context(|| format!("no boundary with seq {seq}"));
    }
    let matches: Vec<&EventRecord> =
        records.iter().filter(|r| cbid(r).starts_with(selector)).collect();
    match matches.len() {
        1 => Ok(matches[0]),
        0 => bail!("no boundary with causal id prefix '{selector}'"),
        _ => {
            let cands: Vec<String> = matches.iter().map(|r| format!("seq {} ({})", r.seq, &cbid(r)[..16])).collect();
            bail!("ambiguous causal id prefix '{selector}' — candidates:\n  {}", cands.join("\n  "))
        }
    }
}

pub fn cmd_show(
    dir: &Path,
    selector: &str,
    want_request: bool,
    want_response: bool,
    want_raw: bool,
    want_meta: bool,
) -> Result<()> {
    let records = load_log(dir).context("loading event log")?;
    let rec = resolve(&records, selector)?;
    let b = decode(&dir.join("objects"), rec);

    if want_raw {
        use std::io::Write;
        std::io::stdout().write_all(&b.raw_bytes)?;
        return Ok(());
    }

    let only_some = want_request || want_response || want_meta;
    let show_req = want_request || !only_some;
    let show_resp = want_response || !only_some;

    println!("boundary seq {}  ({})", rec.seq, cbid(rec));
    println!("kind {}   surface {}", kind_str(rec), surface_str(rec));
    println!(
        "raw object: {}",
        if b.raw_ok {
            format!("✓ matches b3-{}", rec.raw_cid.to_hex())
        } else {
            format!("✗ MISMATCH — displayed bytes do not hash to b3-{}", rec.raw_cid.to_hex())
        }
    );

    if want_meta || !only_some {
        if rec.meta.is_empty() {
            println!("meta: (none)");
        } else {
            println!("meta:");
            for (k, v) in &rec.meta {
                println!("  {k} = {v}");
            }
        }
    }

    if !b.decodable {
        println!("\n(non-decodable surface — use `--raw` to dump the committed bytes)");
        return Ok(());
    }
    if show_req {
        println!("\n── request ──");
        println!("{} {}", b.method.as_deref().unwrap_or("?"), b.url.as_deref().unwrap_or("?"));
        if let Some(body) = &b.req_body {
            println!("{}", pretty_json(body));
        }
    }
    if show_resp {
        println!("\n── response ── {}", b.status.map(|s| s.to_string()).unwrap_or_default());
        if let Some(body) = &b.resp_body {
            println!("{}", pretty_json(body));
        }
    }
    Ok(())
}

// ---------- rewind diff ----------

/// A compact line-level diff: trim the common prefix/suffix and show only the
/// changed lines plus a little context (so a one-field change reads as one hunk,
/// not the whole body). v0 line-level; semantic JSON diff is TODO(phase-1).
fn body_diff(old: &str, new: &str) -> Vec<String> {
    let op = pretty_json(old);
    let np = pretty_json(new);
    if op == np {
        return vec![];
    }
    let o: Vec<&str> = op.lines().collect();
    let n: Vec<&str> = np.lines().collect();

    let mut p = 0;
    while p < o.len() && p < n.len() && o[p] == n[p] {
        p += 1;
    }
    let mut s = 0;
    while s < o.len() - p && s < n.len() - p && o[o.len() - 1 - s] == n[n.len() - 1 - s] {
        s += 1;
    }

    const CTX: usize = 2;
    let mut out = Vec::new();
    let lead = p.saturating_sub(CTX);
    if lead > 0 {
        out.push(format!("  ⋯ {lead} unchanged"));
    }
    for l in &o[lead..p] {
        out.push(format!("  {l}"));
    }
    for l in &o[p..o.len() - s] {
        out.push(format!("- {l}"));
    }
    for l in &n[p..n.len() - s] {
        out.push(format!("+ {l}"));
    }
    let tail_end = (o.len() - s + CTX).min(o.len());
    for l in &o[o.len() - s..tail_end] {
        out.push(format!("  {l}"));
    }
    let tail_rest = o.len() - tail_end;
    if tail_rest > 0 {
        out.push(format!("  ⋯ {tail_rest} unchanged"));
    }
    out
}

#[allow(clippy::too_many_arguments)]
pub fn cmd_diff(
    a: &Path,
    b: &Path,
    stat_only: bool,
    boundary: Option<u64>,
    do_verify: bool,
    pubkey_a: Option<&Path>,
    pubkey_b: Option<&Path>,
) -> Result<i32> {
    if do_verify {
        let v = |dir: &Path, key: Option<&Path>| -> Result<bool> {
            let vk = match key {
                Some(p) => Some(rewind_core::attest::verifying_key_from_hex(
                    &std::fs::read_to_string(p)?,
                )?),
                None => None,
            };
            Ok(verify_artifact(dir, vk.as_ref())?.ok())
        };
        let oa = v(a, pubkey_a)?;
        let ob = v(b, pubkey_b)?;
        println!(
            "trust: {} {}   {} {}",
            a.display(),
            if oa { "VERIFIED ✓" } else { "FAILED ✗" },
            b.display(),
            if ob { "VERIFIED ✓" } else { "FAILED ✗" },
        );
        if !oa || !ob {
            eprintln!("refusing to diff: a side failed verification (the diff would be untrusted)");
            return Ok(2);
        }
    }

    let ra = load_log(a).with_context(|| format!("loading {}", a.display()))?;
    let rb = load_log(b).with_context(|| format!("loading {}", b.display()))?;
    let oa = a.join("objects");
    let ob = b.join("objects");

    // Align in lockstep on causal_boundary_id.
    let mut i = 0;
    let mut prefix = 0;
    let mut changed: Vec<usize> = Vec::new();
    while i < ra.len() && i < rb.len() && ra[i].causal_boundary_id == rb[i].causal_boundary_id {
        // Decide on the FORENSIC committed bytes (raw_cid), not the lossy decoded
        // body — so a status-only swap, a request-field change, or a non-decodable
        // blob all count as a divergence. decode() is only for human rendering.
        if ra[i].raw_cid == rb[i].raw_cid {
            prefix += 1;
        } else {
            changed.push(i); // same request id, different committed bytes = the swap
        }
        i += 1;
    }
    let frontier_a = ra.len() - i; // original-only tail (path not taken)
    let frontier_b = rb.len() - i; // fork-only tail (new branch)

    let diverged_seq = changed.first().copied().or(if frontier_a + frontier_b > 0 { Some(i) } else { None });
    let fork_seq = read_manifest(b).and_then(|m| m.determinism).and_then(|d| d.get("fork_seq").cloned());

    // --stat header.
    print!("prefix: {prefix} identical");
    match diverged_seq {
        Some(d) => print!(" · diverged at seq {}", ra.get(d).map(|r| r.seq).unwrap_or(d as u64)),
        None => print!(" · no divergence"),
    }
    if let Some(fs) = &fork_seq {
        print!(" · forked at seq {fs}");
    }
    println!(" · frontier: +{frontier_b} −{frontier_a}");

    let identical = changed.is_empty() && frontier_a == 0 && frontier_b == 0;

    if !stat_only {
        // CHANGED boundaries (same id, different response) — the counterfactual swap.
        for &c in &changed {
            if boundary.is_some_and(|sel| ra[c].seq != sel) {
                continue;
            }
            println!("\n@@ seq {} ({}) — same request, response changed @@", ra[c].seq, &cbid(&ra[c])[..12]);
            let da = decode(&oa, &ra[c]);
            let db = decode(&ob, &rb[c]);
            if da.status != db.status {
                println!("- status {}", da.status.map(|s| s.to_string()).unwrap_or_else(|| "?".into()));
                println!("+ status {}", db.status.map(|s| s.to_string()).unwrap_or_else(|| "?".into()));
            }
            for line in body_diff(da.resp_body.as_deref().unwrap_or(""), db.resp_body.as_deref().unwrap_or("")) {
                println!("{line}");
            }
        }
        // FRONTIER — never a fabricated 1:1 pairing.
        if (frontier_a > 0 || frontier_b > 0) && boundary.is_none() {
            println!("\n@@ frontier (unaligned — the path not taken vs the counterfactual branch) @@");
            for r in &ra[i..] {
                let d = decode(&oa, r);
                println!("- seq {} {} -> {}", r.seq, d.method.as_deref().unwrap_or("?"), short(d.resp_body.as_deref().unwrap_or(""), 70));
            }
            for r in &rb[i..] {
                let d = decode(&ob, r);
                println!("+ seq {} {} -> {}", r.seq, d.method.as_deref().unwrap_or("?"), short(d.resp_body.as_deref().unwrap_or(""), 70));
            }
        }
    }

    Ok(if identical { 0 } else { 1 })
}
