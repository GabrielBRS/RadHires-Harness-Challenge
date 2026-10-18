use clap::{Parser, Subcommand};
use invoice_check::{
    AppState,
    features::{
        agents::{Agents, catalog},
        checking::{self, models::Invoice},
    },
};
use serde_json::{Value, json};
use std::{io::Read, path::PathBuf};
#[derive(Parser)]
#[command(version, about = "Local invoice review: Rust + MAX")]
struct Cli {
    #[arg(long, env = "INVOICE_ROOT", default_value = ".")]
    root: PathBuf,
    #[arg(long, env = "MAX_URL", default_value = "http://127.0.0.1:8000")]
    max_url: String,
    #[arg(long, env = "MAX_MODEL", default_value = "Qwen/Qwen3-4B")]
    model: String,
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    Serve {
        #[arg(long, default_value_t = 8080)]
        port: u16,
        #[arg(long)]
        demo: bool,
    },
    Agents {
        #[command(subcommand)]
        command: AgentCommand,
    },
    Eval {
        #[arg(long)]
        live: bool,
    },
}
#[derive(Subcommand)]
enum AgentCommand {
    List,
    Doctor,
    Call {
        tool: String,
        #[arg(long)]
        input_file: Option<PathBuf>,
    },
}
#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    if let Err(e) = run().await {
        println!("{}", json!({"ok":false,"error":e.to_string()}));
        std::process::exit(2);
    }
}
async fn run() -> anyhow::Result<()> {
    let cli = Cli::parse();
    let agents = Agents::new(cli.max_url, cli.model)?;
    match cli.command {
        Command::Serve { port, demo } => {
            let state = AppState::new(cli.root, if demo { "demo" } else { "max" }.into(), agents)?;
            let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
            eprintln!("Rust API on http://127.0.0.1:{port}");
            axum::serve(listener, invoice_check::router(state))
                .with_graceful_shutdown(async {
                    let _ = tokio::signal::ctrl_c().await;
                })
                .await?;
        }
        Command::Agents { command } => {
            let data = match command {
                AgentCommand::List => catalog(),
                AgentCommand::Doctor => agents.doctor().await?,
                AgentCommand::Call { tool, input_file } => {
                    let input = if let Some(path) = input_file {
                        std::fs::read_to_string(path)?
                    } else {
                        let mut s = String::new();
                        std::io::stdin().take(64_001).read_to_string(&mut s)?;
                        s
                    };
                    anyhow::ensure!(input.len() <= 64_000, "Input is too large");
                    let value: Value = serde_json::from_str(&input)?;
                    let path = value["pdf_path"]
                        .as_str()
                        .ok_or_else(|| anyhow::anyhow!("pdf_path is required"))?;
                    let path = cli.root.join(path);
                    anyhow::ensure!(
                        std::fs::metadata(&path)?.len() <= 20 * 1024 * 1024,
                        "PDF is too large"
                    );
                    let text = checking::pdf::text(&std::fs::read(path)?)?;
                    match tool.as_str() {
                        "read_invoice" => serde_json::to_value(agents.read(&text).await?)?,
                        "verify_invoice" => {
                            let ex: Invoice = serde_json::from_value(value["extraction"].clone())?;
                            ex.validate()?;
                            serde_json::to_value(agents.verify(&text, &ex).await?)?
                        }
                        "check_invoice" => serde_json::to_value(
                            agents
                                .check(&text, &checking::Reference::default(), &[])
                                .await,
                        )?,
                        _ => anyhow::bail!("Unknown agent tool"),
                    }
                }
            };
            let ok = !data
                .get("agent_runs")
                .and_then(Value::as_array)
                .is_some_and(|t| t.iter().any(|t| t["status"] == "failed"));
            println!("{}", json!({"ok":ok,"data":data}));
            if !ok {
                std::process::exit(2);
            }
        }
        Command::Eval { live } => {
            let rows: Vec<Value> = serde_json::from_str(&std::fs::read_to_string(
                cli.root.join("data/samples/ground_truth.json"),
            )?)?;
            let reference = checking::Reference::default();
            let mut results = vec![];
            let mut correct = 0;
            let mut unsafe_approvals = 0;
            for row in &rows {
                let filename = row["file"].as_str().unwrap();
                let pdf = std::fs::read(cli.root.join("data/samples").join(filename))?;
                let result = match checking::pdf::text(&pdf) {
                    Ok(text) => {
                        if live {
                            agents.check(&text, &reference, &[]).await
                        } else {
                            checking::check(
                                serde_json::from_value(row["extraction"].clone())?,
                                &text,
                                &reference,
                                &[],
                                "oracle",
                            )
                        }
                    }
                    Err(e) => checking::models::CheckResult::failed(
                        "UNREADABLE",
                        &e.to_string(),
                        if live { "max" } else { "oracle" },
                    ),
                };
                let matches = result.decision == row["expected_decision"];
                correct += usize::from(matches);
                unsafe_approvals += usize::from(
                    result.decision == "approve" && row["expected_decision"] != "approve",
                );
                results.push(json!({"file":filename,"expected":row["expected_decision"],"matches":matches,"result":result}));
            }
            let output = json!({"mode":if live{"live-max"}else{"oracle"},"total":rows.len(),"correct":correct,"unsafe_approvals":unsafe_approvals,"results":results});
            println!("{}", serde_json::to_string_pretty(&output)?);
            if correct != rows.len() {
                std::process::exit(1);
            }
        }
    }
    Ok(())
}
