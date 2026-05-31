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
}

fn main() -> Result<()> {
    match Cli::parse().cmd {
        Cmd::Verify { path, pubkey } => cmd_verify(path, pubkey),
        Cmd::Inspect { path } => cmd_inspect(path),
        Cmd::Keygen { out } => cmd_keygen(out),
        Cmd::Demo { path } => cmd_demo(path),
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

fn cmd_inspect(path: PathBuf) -> Result<()> {
    // Reuse the verifier in integrity-only mode for the summary.
    let report = verify_artifact(&path, None).context("reading artifact")?;
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
