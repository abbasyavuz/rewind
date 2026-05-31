//! `rewind` — offline verifier + inspector for `.rewind` artifacts.
//!
//! The verifier is a standalone static binary with NO Python dependency: anyone
//! can confirm an artifact's integrity and signature without trusting us.

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use rewind_core::{
    attest::verifying_key_from_hex, verify_artifact, ArtifactWriter, BoundaryKind, CaptureSurface,
    Cid, Keypair, Profile,
};
use std::collections::BTreeMap;
use std::path::PathBuf;

mod debug;

#[derive(Parser)]
#[command(
    name = "rewind",
    version,
    about = "Rewind — flight recorder for AI agents (offline verifier + tools)"
)]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Verify a .rewind artifact's integrity (and signature, if a pubkey is given).
    Verify {
        /// Path to the .rewind artifact directory.
        path: PathBuf,
        /// Path to a hex-encoded Ed25519 public key (omit to skip signature check).
        #[arg(long)]
        pubkey: Option<PathBuf>,
    },
    /// Print a human summary of a .rewind artifact.
    Inspect {
        path: PathBuf,
        /// Emit the integrity report as JSON.
        #[arg(long)]
        json: bool,
    },
    /// Generate an Ed25519 keypair: writes <out> (secret) and <out>.pub (public).
    Keygen {
        out: PathBuf,
    },
    /// Write a tiny sample .rewind artifact for testing the verifier.
    Demo {
        /// Output directory for the artifact.
        path: PathBuf,
    },
    /// Time-travel timeline: one row per captured boundary (pipe to `less -R`).
    Log {
        path: PathBuf,
        #[arg(long)]
        json: bool,
        #[arg(long)]
        oneline: bool,
        #[arg(long)]
        kind: Option<String>,
        #[arg(long)]
        surface: Option<String>,
        #[arg(long)]
        from: Option<u64>,
        #[arg(long)]
        to: Option<u64>,
        #[arg(short = 'n')]
        limit: Option<usize>,
    },
    /// Show one boundary's full request + response (by seq or causal-id prefix).
    Show {
        path: PathBuf,
        /// A boundary seq (integer) or a (short) causal_boundary_id.
        selector: String,
        #[arg(long)]
        request: bool,
        #[arg(long)]
        response: bool,
        #[arg(long)]
        raw: bool,
        #[arg(long)]
        meta: bool,
    },
    /// Diff two trajectories (e.g. original vs forked): prefix · divergence · frontier.
    Diff {
        a: PathBuf,
        b: PathBuf,
        /// Only print the --stat header.
        #[arg(long)]
        stat: bool,
        /// Drill into a single boundary's response diff.
        #[arg(long)]
        boundary: Option<u64>,
        /// Verify both artifacts before diffing; refuse if either fails.
        #[arg(long)]
        verify: bool,
        #[arg(long)]
        pubkey_a: Option<PathBuf>,
        #[arg(long)]
        pubkey_b: Option<PathBuf>,
    },
}

fn main() -> Result<()> {
    match Cli::parse().cmd {
        Cmd::Verify { path, pubkey } => cmd_verify(path, pubkey),
        Cmd::Inspect { path, json } => cmd_inspect(path, json),
        Cmd::Keygen { out } => cmd_keygen(out),
        Cmd::Demo { path } => cmd_demo(path),
        Cmd::Log { path, json, oneline, kind, surface, from, to, limit } => {
            debug::cmd_log(&path, json, oneline, kind, surface, from, to, limit)
        }
        Cmd::Show { path, selector, request, response, raw, meta } => {
            debug::cmd_show(&path, &selector, request, response, raw, meta)
        }
        Cmd::Diff { a, b, stat, boundary, verify, pubkey_a, pubkey_b } => {
            let code = debug::cmd_diff(&a, &b, stat, boundary, verify, pubkey_a.as_deref(), pubkey_b.as_deref())?;
            std::process::exit(code);
        }
    }
}

fn cmd_verify(path: PathBuf, pubkey: Option<PathBuf>) -> Result<()> {
    let vk = match pubkey {
        Some(p) => {
            let hexkey = std::fs::read_to_string(&p)
                .with_context(|| format!("reading pubkey {}", p.display()))?;
            Some(verifying_key_from_hex(&hexkey).context("parsing pubkey")?)
        }
        None => None,
    };
    let report = verify_artifact(&path, vk.as_ref()).context("verifying artifact")?;

    let mark = |b: bool| if b { "✓" } else { "✗" };
    println!("artifact : {}", path.display());
    println!("run_id   : {}", report.run_id);
    println!("events   : {}", report.event_count);
    println!("[{}] hash chain", mark(report.chain_ok));
    println!("[{}] merkle root", mark(report.merkle_ok));
    println!("[{}] raw objects", mark(report.raw_objects_ok));
    println!("[{}] redaction auditable", mark(report.redaction_auditable));
    match report.signature_ok {
        Some(b) => println!("[{}] signature", mark(b)),
        None => println!("[-] signature (no pubkey supplied; integrity-only)"),
    }

    if report.ok() {
        println!("\nVERIFIED ✓");
        Ok(())
    } else {
        println!("\nFAILED ✗");
        std::process::exit(1);
    }
}

fn cmd_inspect(path: PathBuf, json: bool) -> Result<()> {
    // Reuse the verifier in integrity-only mode for the summary.
    let report = verify_artifact(&path, None).context("reading artifact")?;
    if json {
        let v = serde_json::json!({
            "run_id": report.run_id,
            "event_count": report.event_count,
            "chain_ok": report.chain_ok,
            "merkle_ok": report.merkle_ok,
            "raw_objects_ok": report.raw_objects_ok,
            "redaction_auditable": report.redaction_auditable,
            "signature_ok": report.signature_ok,
        });
        println!("{v}");
        return Ok(());
    }
    println!("run_id : {}", report.run_id);
    println!("events : {}", report.event_count);
    println!("chain  : {}", if report.chain_ok { "intact" } else { "BROKEN" });
    println!("merkle : {}", if report.merkle_ok { "ok" } else { "MISMATCH" });
    println!("(run `rewind verify {} --pubkey <key.pub>` to check the signature)", path.display());
    Ok(())
}

fn cmd_keygen(out: PathBuf) -> Result<()> {
    let kp = Keypair::generate();
    std::fs::write(&out, hex::encode(kp.secret_bytes()))
        .with_context(|| format!("writing secret key {}", out.display()))?;
    let pubpath = out.with_extension("pub");
    let pubhex = hex::encode(kp.verifying_key().to_bytes());
    std::fs::write(&pubpath, &pubhex)?;
    println!("secret key : {}", out.display());
    println!("public key : {} ({})", pubpath.display(), kp.keyid());
    println!("\n⚠ Keep the secret key out of git (.gitignore already excludes *.key/keys/).");
    Ok(())
}

fn cmd_demo(path: PathBuf) -> Result<()> {
    let kp = Keypair::generate();
    let mut w = ArtifactWriter::create(&path, "demo-run", Profile::RecordOnly, 1)
        .context("creating artifact")?;

    let mut parent = Cid::ZERO;
    for i in 0..3u64 {
        let raw = format!("{{\"demo_boundary\": {i}}}").into_bytes();
        let mut meta = BTreeMap::new();
        meta.insert("provider".into(), "demo".into());
        let (_h, cbid) = w.append_boundary(
            BoundaryKind::ModelCall,
            CaptureSurface::SdkHttpx,
            parent,
            format!("request-{i}").as_bytes(),
            &raw,
            None,
            None,
            meta,
            1_700_000_000_000 + i,
        )?;
        parent = cbid;
    }
    w.finalize(&kp)?;

    let pubpath = path.join("demo-key.pub");
    std::fs::write(&pubpath, hex::encode(kp.verifying_key().to_bytes()))?;
    println!("wrote demo artifact: {}", path.display());
    println!("verify it:\n  rewind verify {} --pubkey {}", path.display(), pubpath.display());
    Ok(())
}
