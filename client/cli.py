"""
client/cli.py

Command-line interface for VerdictFS.

Commands:
  python -m client.cli put <filepath> [--verbose]
  python -m client.cli get <key> <output> [--chunk-count N]
  python -m client.cli status <key>
"""

import asyncio
import logging
import os
from pathlib import Path

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s  %(name)s — %(message)s",
)

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box

from client.client import VerdictFSClient

app = typer.Typer(name="verdictfs", help="Fault-tolerant distributed file system")
console = Console()


def _make_client(verbose: bool = False) -> VerdictFSClient:
    urls = [
        u.strip()
        for u in os.environ.get(
            "SERVER_URLS",
            "http://localhost:5000,http://localhost:5001,http://localhost:5002,"
            "http://localhost:5003,http://localhost:5004",
        ).split(",")
    ]
    n = int(os.environ.get("N", len(urls)))
    m = int(os.environ.get("M", 3))
    return VerdictFSClient(server_urls=urls, n=n, m=m, verbose=verbose)


# ---------------------------------------------------------------------------
# put
# ---------------------------------------------------------------------------

@app.command()
def put(
    filepath: Path = typer.Argument(..., help="Local file to upload"),
    key: str = typer.Option(None, help="Object key (default: filename)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show encoding details"),
):
    """Disperse a file to the VerdictFS cluster."""
    if not filepath.exists():
        console.print(f"[red]File not found: {filepath}[/red]")
        raise typer.Exit(1)

    key = key or filepath.name
    data = filepath.read_bytes()
    client = _make_client(verbose)

    console.print(f"\n[bold cyan]VerdictFS put[/bold cyan]  {filepath}  →  [bold]{key}[/bold]")
    console.print(f"File size: [yellow]{len(data):,}[/yellow] bytes\n")

    result = asyncio.run(client.put(key, data))

    # ---- encoding breakdown ----
    if verbose:
        for cr in result.chunks:
            console.print(Panel(
                f"[bold]Chunk {cr.chunk_index}[/bold]  ({cr.original_size:,} bytes  →  "
                f"{cr.fragment_size} bytes/fragment)",
                style="dim"
            ))

            # Fragment table
            frag_table = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta")
            frag_table.add_column("Fragment", justify="center", width=10)
            frag_table.add_column("Type", width=12)
            frag_table.add_column("Size", justify="right", width=8)
            frag_table.add_column("SHA-256 (cc)", width=22)
            frag_table.add_column("Fingerprint (fp)", width=36)

            n = client.n
            m = client.m
            for i in range(n):
                ftype = "[green]systematic[/green]" if i < m else "[yellow]parity[/yellow]"
                fp_val = cr.fpcc_fp[i] if i < m else "[dim]derived from fp[0..m-1][/dim]"
                frag_table.add_row(
                    f"frag[{i}]",
                    ftype,
                    f"{cr.fragment_size}B",
                    cr.fpcc_cc[i],
                    fp_val,
                )
            console.print(frag_table)

            # Server response table
            resp_table = Table(box=box.SIMPLE, show_header=True, header_style="bold blue")
            resp_table.add_column("Server", width=8)
            resp_table.add_column("HTTP", justify="center", width=6)
            resp_table.add_column("Result", width=20)
            for r in cr.server_responses:
                code = r.get("status", "err")
                ok = code == 200
                body = r.get("body", {})
                resp_table.add_row(
                    f"server{r['server']}",
                    f"[green]{code}[/green]" if ok else f"[red]{code}[/red]",
                    body.get("status", r.get("error", "?")),
                )
            console.print(resp_table)

    # ---- AVID-FP round status ----
    # Chunker stores under "key/chunk/0" — query that, not the bare key
    from fs.chunker import chunk_key as _ckey
    status_key = _ckey(key, 0)
    console.print(f"[dim]Waiting for echo/ready rounds (checking: {status_key})...[/dim]")
    statuses = asyncio.run(client.wait_for_status(status_key))

    status_table = Table(
        title=f"AVID-FP Status  —  key: [bold]{key}[/bold]",
        box=box.ROUNDED,
        header_style="bold",
    )
    status_table.add_column("Server", style="cyan", width=10)
    status_table.add_column("Verified", justify="center", width=10)
    status_table.add_column("Echoes", justify="center", width=8)
    status_table.add_column("Readys", justify="center", width=8)
    status_table.add_column("Stored", justify="center", width=10)

    n = client.n
    f = (n - client.m)  # f = n - m, approx
    for s in statuses:
        if "error" in s:
            status_table.add_row(f"server{s['server']}", "[red]error[/red]", "-", "-", "-")
        else:
            stored_icon  = "[green]✓ stored[/green]"  if s["stored"]   else "[yellow]pending[/yellow]"
            verified_icon = "[green]✓[/green]"        if s["verified"] else "[red]✗[/red]"
            echo_color   = "green" if s["echo_count"] >= (n - 1) else "yellow"
            ready_color  = "green" if s["ready_count"] >= (2 * 1 + 1) else "yellow"
            status_table.add_row(
                f"server{s['server']}",
                verified_icon,
                f"[{echo_color}]{s['echo_count']}[/{echo_color}]",
                f"[{ready_color}]{s['ready_count']}[/{ready_color}]",
                stored_icon,
            )

    console.print(status_table)

    stored_count = sum(1 for s in statuses if s.get("stored"))
    if stored_count >= client.m:
        console.print(f"\n[bold green]✓ Successfully stored on {stored_count}/{n} servers[/bold green]")
        console.print(f"  (need any {client.m} to reconstruct)\n")
    else:
        console.print(f"\n[yellow]⚠ Only stored on {stored_count}/{n} servers so far[/yellow]\n")


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

@app.command()
def get(
    key: str = typer.Argument(..., help="Object key to retrieve"),
    output: Path = typer.Argument(..., help="Local path to write retrieved data"),
    chunk_count: int = typer.Option(1, help="Number of chunks (from metadata)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Retrieve a file from the VerdictFS cluster."""
    client = _make_client(verbose)
    console.print(f"\n[bold cyan]VerdictFS get[/bold cyan]  {key}  →  {output}")

    # Attempt retrieval — server results come back alongside the data
    success = False
    get_result = None
    error_msg = ""
    try:
        get_result = asyncio.run(client.get(key, chunk_count))
        success = True
    except RuntimeError as e:
        error_msg = str(e)

    # Build table from the results already collected during retrieval
    title = (
        f"[green]✓ Retrieval succeeded[/green]  —  key: {key}"
        if success else
        f"[red]✗ Retrieval failed[/red]  —  key: {key}"
    )
    t = Table(title=title, box=box.ROUNDED, header_style="bold")
    t.add_column("Server",        style="cyan", width=10)
    t.add_column("Reachable",     justify="center", width=10)
    t.add_column("Has fragment",  justify="center", width=14)
    t.add_column("FPCC verified", justify="center", width=14)
    t.add_column("Used",          justify="center", width=8)

    srv_results = get_result.server_results if get_result else []
    have = sum(1 for s in srv_results if s.had_fragment)

    for s in sorted(srv_results, key=lambda x: x.server):
        if not s.reachable:
            t.add_row(f"server{s.server}", "[red]✗ offline[/red]", "-", "-", "-")
        else:
            fpcc_col = (
                "[green]✓ pass[/green]"      if s.fpcc_verified else
                "[red]✗ FAIL — lying[/red]"  if s.had_fragment  else
                "[dim]—[/dim]"
            )
            t.add_row(
                f"server{s.server}",
                "[green]✓[/green]",
                "[green]✓[/green]" if s.had_fragment else "[yellow]✗[/yellow]",
                fpcc_col,
                "[green]✓[/green]" if s.used else "[dim]—[/dim]",
            )

    console.print(t)

    if success:
        output.write_bytes(get_result.data)
        console.print(f"[bold green]✓ Retrieved {len(get_result.data):,} bytes  →  {output}[/bold green]")
        console.print(f"  ({have}/{client.n} fragments available, needed {client.m})\n")
    else:
        console.print(f"[bold red]✗ {error_msg}[/bold red]")
        console.print(f"  ({have}/{client.n} fragments available, needed {client.m})\n")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status(
    key: str = typer.Argument(..., help="Object key to query"),
):
    """Show per-server AVID-FP status for an object key."""
    client = _make_client()
    console.print(f"\n[bold cyan]VerdictFS status[/bold cyan]  key=[bold]{key}[/bold]\n")
    statuses = asyncio.run(client.wait_for_status(key, timeout=5.0))

    table = Table(box=box.ROUNDED, header_style="bold")
    table.add_column("Server",   style="cyan", width=10)
    table.add_column("Verified", justify="center", width=10)
    table.add_column("Echoes",   justify="center", width=8)
    table.add_column("Readys",   justify="center", width=8)
    table.add_column("Stored",   justify="center", width=12)

    for s in statuses:
        if "error" in s:
            table.add_row(f"server{s['server']}", "[red]error[/red]", "-", "-", "-")
        else:
            table.add_row(
                f"server{s['server']}",
                "[green]✓[/green]" if s["verified"] else "[red]✗[/red]",
                str(s["echo_count"]),
                str(s["ready_count"]),
                "[green]✓ stored[/green]" if s["stored"] else "[yellow]pending[/yellow]",
            )

    console.print(table)


if __name__ == "__main__":
    app()